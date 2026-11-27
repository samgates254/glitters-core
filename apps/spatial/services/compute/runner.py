"""Durable compute jobs. Postgres holds the job. Rental APIs are not called."""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Count, Sum
from django.utils import timezone

from apps.spatial.models import ComputeEntitlement, ComputeJob, ComputeJobEvent, GeospatialAsset
from apps.spatial.services.asset_registry import assert_room, store_bundle, store_bytes
from apps.spatial.services.compute.contracts import (
    ACTUAL_NOTE,
    ASSET_DERIVED,
    ASSET_SOURCE,
    CANCELLED,
    COMMERCIAL_PROVIDERS,
    ESTIMATE_NOTE,
    FAILED,
    FIXTURE_TRIANGLE,
    JOB_STATES,
    LOCAL_CPU,
    LOCAL_GPU,
    MOCK_RENTED_GPU,
    OPERATION_GLTF,
    PROVENANCE_MODELLED,
    PROVENANCE_USER,
    PROVIDER_IDS,
    QUEUED,
    QUEUED_STATES,
    RUNNING,
    SIMULATE_FAIL,
    SIMULATE_FAIL_ONCE,
    SUCCEEDED,
    SURFACE_OPERATIONS,
    SURFACE_PRODUCT_REVISIONS,
    TERMINAL_STATES,
    VERIFYING,
    ComputeError,
    ComputeUnavailable,
    DatasetTooLarge,
    JobAlreadyTerminal,
    PermanentComputeError,
    ProviderNotConfigured,
    RecoverableComputeError,
    RemoteRenderDisabled,
)
from apps.spatial.services.compute.gltf_job import (
    canonical_bytes,
    normalize_gltf,
    sha256_hex,
    triangle_fixture,
    validate_gltf,
)
from apps.spatial.services.compute.preflight import host_snapshot, preflight_dataset, probe_gpu
from apps.spatial.services.compute.providers import (
    adapter_status,
    commercial_refusal,
    open_remote_render,
    provider_catalog,
)
from apps.spatial.services.compute.storage import file_exists, read_bytes, remove_file
from apps.spatial.services.compute.surfaces import RESERVE_BYTES, produce_surface
from apps.spatial.services.safe_paths import UnsafeSpatialPath

CAPABILITY_MINUTES = 15
ZERO = Decimal("0.0000")


def money(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return f"{value.quantize(Decimal('0.0001')):.4f}"


def _audit(job: ComputeJob, message: str) -> None:
    ComputeJobEvent.objects.create(
        job=job,
        organization_id=job.organization_id,
        state=job.state,
        message=message[:300],
    )


def _issue_capability(job: ComputeJob) -> None:
    """Hash a one-time token and drop the token. It is not stored or queued."""

    token = secrets.token_urlsafe(32)
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    job.capability_hash = digest
    job.capability_expires_at = timezone.now() + timedelta(minutes=CAPABILITY_MINUTES)
    job.save(update_fields=["capability_hash", "capability_expires_at", "updated_at"])
    job.refresh_from_db(fields=["capability_hash"])
    presented = hashlib.sha256(token.encode("utf-8")).hexdigest()
    if not hmac.compare_digest(presented, job.capability_hash):
        raise PermanentComputeError("Capability check failed before the token was discarded.")
    _audit(job, "Short-lived capability issued inside the trusted application process.")


def _authorize_app_worker(job: ComputeJob) -> None:
    """The Celery worker is this application, not a rented GPU.

    The raw token is never sent through the broker. A remote worker cannot
    reconstruct it, and the database URL is not attached to the job.
    """

    if job.provider_id in COMMERCIAL_PROVIDERS:
        raise ProviderNotConfigured("Commercial providers are not dispatched.")
    if not job.capability_hash:
        raise PermanentComputeError("Job capability is missing.")
    expires = job.capability_expires_at
    if expires is None or expires <= timezone.now():
        raise PermanentComputeError("Job capability has expired.")


def _burn_capability(job: ComputeJob) -> None:
    job.capability_hash = ""
    job.capability_expires_at = timezone.now()


def _cleanup(job: ComputeJob, *, published: bool) -> dict:
    retained = False
    if job.input_asset_id and job.input_asset.storage_key:
        retained = file_exists(str(job.organization_id), job.input_asset.storage_key)
    return {
        "released": True,
        "provider_id": job.provider_id,
        "rented_instance": False,
        "source_retained": retained,
        "output_published": published,
        "capability_burned": True,
    }


def _store_source(organization, user, payload: bytes) -> GeospatialAsset:
    return store_bytes(
        organization=organization,
        user=user,
        payload=payload,
        kind=ASSET_SOURCE,
        media_type="model/gltf+json",
        provenance=PROVENANCE_USER,
        asset_type="gltf",
        status="stored",
        suffix=".gltf",
        source_note="User-supplied glTF retained for the compute job.",
    )


def _store_derived(job: ComputeJob, payload: bytes) -> GeospatialAsset:
    return store_bytes(
        organization=job.organization,
        user=job.created_by,
        payload=payload,
        kind=ASSET_DERIVED,
        media_type="model/gltf+json",
        provenance=PROVENANCE_MODELLED,
        asset_type="gltf",
        status="published",
        suffix=".gltf",
        source_note="Normalized glTF. Not a reconstruction or a survey.",
    )


def document_bytes(*, fixture: str, document: dict | None) -> bytes:
    if fixture and document is not None:
        raise PermanentComputeError("Send a fixture id or a document, not both.")
    if fixture:
        if fixture != FIXTURE_TRIANGLE:
            raise PermanentComputeError("Unknown fixture.")
        return canonical_bytes(triangle_fixture())
    if not isinstance(document, dict):
        raise PermanentComputeError("A glTF JSON object is required.")
    return canonical_bytes(document)


def submit_job(
    *,
    organization,
    user,
    provider_id: str,
    operation: str,
    fixture: str = "",
    document: dict | None = None,
    idempotency_key: str = "",
    simulate: str = "",
    place: str = "",
) -> tuple[ComputeJob, bool]:
    if provider_id not in PROVIDER_IDS:
        raise PermanentComputeError("Unknown compute provider.")
    if operation != OPERATION_GLTF and operation not in SURFACE_OPERATIONS:
        raise PermanentComputeError(
            "That operation is not available in the local orchestration layer."
        )
    if simulate and provider_id != MOCK_RENTED_GPU:
        raise PermanentComputeError("Failure simulation is only available on the mock provider.")
    if simulate and simulate not in {SIMULATE_FAIL, SIMULATE_FAIL_ONCE}:
        raise PermanentComputeError("Unknown failure simulation.")
    key = (idempotency_key or "")[:80]
    if key:
        existing = (
            ComputeJob.objects.filter(organization=organization, idempotency_key=key)
            .select_related("input_asset", "output_asset")
            .first()
        )
        if existing:
            return existing, False

    if provider_id in COMMERCIAL_PROVIDERS:
        entitled = ComputeEntitlement.objects.filter(
            organization=organization, provider_id=provider_id
        ).exists()
        decision = commercial_refusal(provider_id, entitled=entitled)
        job = ComputeJob.objects.create(
            organization=organization,
            created_by=user,
            provider_id=provider_id,
            operation=operation,
            state=FAILED,
            progress=0,
            attempt=0,
            max_attempts=1,
            error_class="ProviderNotConfigured",
            error_detail=decision["reason"][:300],
            source_retained=False,
            cleanup={
                "released": True,
                "provider_id": provider_id,
                "rented_instance": False,
                "source_retained": False,
                "output_published": False,
                "dataset_retained": False,
            },
            estimated_cost_amount=None,
            actual_cost_amount=ZERO,
            external_request_count=0,
            preflight={"dataset_retained": False, "workload_class": "LIGHT"},
            provenance={"commercial": decision, "remote_worker": False},
            idempotency_key=key,
            simulate="",
            finished_at=timezone.now(),
        )
        _audit(job, decision["reason"])
        return job, True

    if operation in SURFACE_OPERATIONS:
        return _submit_surface(
            organization=organization,
            user=user,
            provider_id=provider_id,
            operation=operation,
            place=place,
            idempotency_key=key,
        )

    payload = document_bytes(fixture=fixture, document=document)
    snapshot = preflight_dataset(len(payload))
    if provider_id == LOCAL_GPU and snapshot["gpu"]["status"] != "available":
        source = _store_source(organization, user, payload)
        job = ComputeJob.objects.create(
            organization=organization,
            created_by=user,
            provider_id=LOCAL_GPU,
            operation=operation,
            state=FAILED,
            error_class="ComputeUnavailable",
            error_detail=snapshot["gpu"]["reason"][:300],
            input_asset=source,
            source_retained=True,
            cleanup={
                "released": True,
                "provider_id": LOCAL_GPU,
                "rented_instance": False,
                "source_retained": True,
                "output_published": False,
                "fallback_provider_id": LOCAL_CPU,
            },
            estimated_cost_amount=None,
            actual_cost_amount=ZERO,
            external_request_count=0,
            preflight=snapshot,
            provenance={"remote_worker": False, "reconstruction": False},
            idempotency_key=key,
            finished_at=timezone.now(),
        )
        _audit(job, "Local GPU is unavailable. No rental provider was contacted.")
        return job, True

    source = _store_source(organization, user, payload)
    max_attempts = 2 if provider_id == MOCK_RENTED_GPU else 1
    try:
        job = ComputeJob.objects.create(
            organization=organization,
            created_by=user,
            provider_id=provider_id,
            operation=operation,
            state=QUEUED,
            progress=5,
            attempt=0,
            max_attempts=max_attempts,
            input_asset=source,
            source_retained=True,
            estimated_cost_amount=None,
            actual_cost_amount=ZERO,
            external_request_count=0,
            preflight=snapshot,
            provenance={},
            idempotency_key=key,
            simulate=simulate,
        )
    except IntegrityError:
        remove_file(str(organization.id), source.storage_key)
        source.delete()
        existing = ComputeJob.objects.get(organization=organization, idempotency_key=key)
        return existing, False
    _audit(job, "Job queued on the trusted application process.")
    _issue_capability(job)
    _enqueue(str(job.id))
    job.refresh_from_db()
    return job, True


def _enqueue(job_id: str) -> None:
    from apps.spatial.tasks import run_compute_job

    if getattr(settings, "GLITTERS_COMPUTE_EAGER", True):
        run_compute_job.apply(args=[job_id])
        return
    run_compute_job.delay(job_id)


def _submit_surface(
    *,
    organization,
    user,
    provider_id: str,
    operation: str,
    place: str,
    idempotency_key: str,
) -> tuple[ComputeJob, bool]:
    chosen = (place or "").strip().lower()
    if chosen not in {"nairobi", "kisumu"}:
        raise PermanentComputeError("Choose Nairobi or Kisumu for this surface.")
    prior = (
        ComputeJob.objects.filter(
            organization=organization,
            operation=operation,
            state=SUCCEEDED,
            preflight__place=chosen,
        )
        .select_related("input_asset", "output_asset")
        .order_by("-created_at")
        .first()
    )
    expected_revision = SURFACE_PRODUCT_REVISIONS.get(operation)
    prior_revision = (prior.preflight or {}).get("product_revision") if prior else None
    if (
        prior
        and prior.output_asset_id
        and prior.output_asset.deleted_at is None
        and not idempotency_key
        and prior_revision == expected_revision
    ):
        return prior, False
    if provider_id == LOCAL_GPU:
        snapshot = preflight_dataset(1)
        if snapshot["gpu"]["status"] != "available":
            job = ComputeJob.objects.create(
                organization=organization,
                created_by=user,
                provider_id=LOCAL_GPU,
                operation=operation,
                state=FAILED,
                error_class="ComputeUnavailable",
                error_detail=snapshot["gpu"]["reason"][:300],
                source_retained=False,
                cleanup={
                    "released": True,
                    "provider_id": LOCAL_GPU,
                    "rented_instance": False,
                    "source_retained": False,
                    "output_published": False,
                    "fallback_provider_id": LOCAL_CPU,
                },
                estimated_cost_amount=None,
                actual_cost_amount=ZERO,
                external_request_count=0,
                preflight={**snapshot, "place": chosen, "dataset_retained": False},
                provenance={"remote_worker": False, "reconstruction": False},
                idempotency_key=idempotency_key,
                finished_at=timezone.now(),
            )
            _audit(job, "Local GPU is unavailable. No rental provider was contacted.")
            return job, True
        raise PermanentComputeError("Terrain and building publication runs on the local CPU.")
    if provider_id != LOCAL_CPU:
        raise PermanentComputeError("City surfaces run on the local CPU.")
    snapshot = preflight_dataset(RESERVE_BYTES)
    assert_room(organization, RESERVE_BYTES)
    snapshot["place"] = chosen
    if expected_revision:
        snapshot["product_revision"] = expected_revision
    snapshot["estimated_bytes"] = RESERVE_BYTES
    snapshot["gpu_required"] = False
    snapshot["rental"] = False
    job = ComputeJob.objects.create(
        organization=organization,
        created_by=user,
        provider_id=LOCAL_CPU,
        operation=operation,
        state=QUEUED,
        progress=5,
        input_asset=None,
        source_retained=False,
        estimated_cost_amount=None,
        actual_cost_amount=ZERO,
        external_request_count=0,
        preflight=snapshot,
        provenance={"reconstruction": False, "photogrammetry": False, "place": chosen},
        idempotency_key=idempotency_key,
    )
    _audit(job, "Surface job queued. No rented GPU was requested.")
    _issue_capability(job)
    _enqueue(str(job.id))
    job.refresh_from_db()
    return job, True


def _run_surface(job_id: str) -> str:
    with transaction.atomic():
        job = ComputeJob.objects.select_for_update(of=("self",)).get(pk=job_id)
        if job.state in TERMINAL_STATES:
            return job.state
        _authorize_app_worker(job)
        job.attempt += 1
        job.state = RUNNING
        job.progress = 40
        job.started_at = job.started_at or timezone.now()
        job.external_request_count = 0
        job.save()
        _audit(job, f"Attempt {job.attempt} started.")
        operation = job.operation
        place = str((job.preflight or {}).get("place") or "")
        organization = job.organization
        user = job.created_by
    try:
        produced = produce_surface(operation, place)
        source = store_bytes(
            organization=organization,
            user=user,
            payload=produced["source_bytes"],
            kind=ASSET_SOURCE,
            media_type=produced["source_media"],
            provenance=produced["source_provenance"],
            asset_type=produced["source_type"],
            status="stored",
            suffix=produced["source_suffix"],
            license_identifier=produced["license_identifier"],
            attribution=produced["attribution"],
            source_note=produced["note"],
        )
    except ComputeError as exc:
        _fail(job_id, exc)
        return FAILED
    with transaction.atomic():
        locked = ComputeJob.objects.select_for_update(of=("self",)).get(pk=job_id)
        if locked.state in TERMINAL_STATES:
            return locked.state
        locked.input_asset = source
        locked.source_retained = True
        locked.save(update_fields=["input_asset", "source_retained", "updated_at"])
    try:
        if produced.get("output_files"):
            output = store_bundle(
                organization=organization,
                user=user,
                files=produced["output_files"],
                kind=ASSET_DERIVED,
                media_type=produced["output_media"],
                provenance=produced["output_provenance"],
                asset_type=produced["output_type"],
                status="published",
                license_identifier=produced["license_identifier"],
                attribution=produced["attribution"],
                source_note=produced["note"],
            )
        else:
            output = store_bytes(
                organization=organization,
                user=user,
                payload=produced["output_bytes"],
                kind=ASSET_DERIVED,
                media_type=produced["output_media"],
                provenance=produced["output_provenance"],
                asset_type=produced["output_type"],
                status="published",
                suffix=produced["output_suffix"],
                license_identifier=produced["license_identifier"],
                attribution=produced["attribution"],
                source_note=produced["note"],
            )
    except ComputeError as exc:
        _fail(job_id, exc)
        return FAILED
    _finish_surface(job_id, output, produced["provenance"])
    return SUCCEEDED


def _finish_surface(job_id: str, output: GeospatialAsset, extra: dict) -> None:
    with transaction.atomic():
        job = (
            ComputeJob.objects.select_for_update(of=("self",))
            .select_related("input_asset", "output_asset")
            .get(pk=job_id)
        )
        if job.state in TERMINAL_STATES:
            return
        job.state = VERIFYING
        job.progress = 80
        job.save(update_fields=["state", "progress", "updated_at"])
        _audit(job, "Verifying the derived asset hash before publication.")
        readback = read_bytes(str(job.organization_id), output.storage_key)
        if sha256_hex(readback) != output.sha256:
            raise PermanentComputeError("Published hash does not match the stored asset.")
        job.output_asset = output
        job.state = SUCCEEDED
        job.progress = 100
        job.error_class = ""
        job.error_detail = ""
        job.estimated_cost_amount = None
        job.actual_cost_amount = ZERO
        job.external_request_count = 0
        job.source_retained = True
        job.finished_at = timezone.now()
        job.provenance = {
            "operation": job.operation,
            "provider_id": job.provider_id,
            "source_sha256": job.input_asset.sha256 if job.input_asset_id else "",
            "output_sha256": output.sha256,
            "remote_worker": False,
            "processing_gpu_required_to_view": False,
            "transport": "in-process",
            "production_credentials_sent": False,
            "output_provenance": PROVENANCE_MODELLED,
            "source_provenance": job.input_asset.provenance if job.input_asset_id else "",
            **extra,
        }
        _burn_capability(job)
        job.cleanup = _cleanup(job, published=True)
        job.save()
        _audit(job, "Derived surface published after hash verification.")


def execute_job(job_id: str) -> str:
    try:
        job = ComputeJob.objects.select_related("input_asset", "output_asset").get(pk=job_id)
    except ComputeJob.DoesNotExist:
        return FAILED
    if job.state in TERMINAL_STATES:
        return job.state
    try:
        if job.operation in SURFACE_OPERATIONS:
            return _run_surface(job_id)
        return _run_attempts(job_id)
    except ComputeError as exc:
        _fail(job_id, exc)
        return FAILED


def _run_attempts(job_id: str) -> str:
    while True:
        with transaction.atomic():
            job = (
                ComputeJob.objects.select_for_update(of=("self",))
                .select_related("input_asset", "output_asset")
                .get(pk=job_id)
            )
            if job.state in TERMINAL_STATES:
                return job.state
            _authorize_app_worker(job)
            job.attempt += 1
            job.state = RUNNING
            job.progress = 45
            job.started_at = job.started_at or timezone.now()
            job.external_request_count = 0
            job.save()
            _audit(job, f"Attempt {job.attempt} started.")
            attempt = job.attempt
            max_attempts = job.max_attempts
            simulate = job.simulate
            provider_id = job.provider_id
            organization_id = str(job.organization_id)
            storage_key = job.input_asset.storage_key
            source_sha = job.input_asset.sha256
        try:
            normalized = _perform(
                job_id=job_id,
                provider_id=provider_id,
                organization_id=organization_id,
                storage_key=storage_key,
                source_sha256=source_sha,
                simulate=simulate,
                attempt=attempt,
            )
        except RecoverableComputeError as exc:
            if attempt >= max_attempts:
                _fail(job_id, exc)
                return FAILED
            _mark_retry(job_id, exc)
            continue
        _publish(job_id, normalized)
        return SUCCEEDED


def _perform(
    *,
    job_id: str,
    provider_id: str,
    organization_id: str,
    storage_key: str,
    source_sha256: str,
    simulate: str,
    attempt: int,
) -> bytes:
    if provider_id in COMMERCIAL_PROVIDERS:
        raise ProviderNotConfigured(commercial_refusal(provider_id, entitled=False)["reason"])
    if provider_id == LOCAL_GPU:
        gpu = probe_gpu()
        if gpu["status"] != "available":
            raise ComputeUnavailable(gpu["reason"] or "Local GPU is unavailable.")
    if provider_id == MOCK_RENTED_GPU and simulate == SIMULATE_FAIL:
        raise RecoverableComputeError("Simulated rented GPU failure before publication.")
    if provider_id == MOCK_RENTED_GPU and simulate == SIMULATE_FAIL_ONCE and attempt == 1:
        raise RecoverableComputeError("Simulated rented GPU failure before publication.")
    try:
        raw = read_bytes(organization_id, storage_key)
    except UnsafeSpatialPath as exc:
        raise PermanentComputeError("Job input is not readable inside its organization.") from exc
    if sha256_hex(raw) != source_sha256:
        raise PermanentComputeError("Source hash does not match the stored asset.")
    limit = int(getattr(settings, "GLITTERS_COMPUTE_MAX_BYTES", 2_000_000))
    if len(raw) > limit:
        raise DatasetTooLarge("Dataset exceeds the CPU development limit. It was not published.")
    try:
        document = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise PermanentComputeError("Stored glTF is not valid JSON.") from exc
    source = validate_gltf(document)
    normalized = normalize_gltf(document, job_id=job_id, source_sha256=source_sha256)
    output = validate_gltf(json.loads(normalized))
    if output.mesh_count != source.mesh_count or output.mesh_count < 1:
        raise PermanentComputeError("Output mesh count does not match the source.")
    return normalized


def _mark_retry(job_id: str, exc: RecoverableComputeError) -> None:
    with transaction.atomic():
        job = ComputeJob.objects.select_for_update(of=("self",)).get(pk=job_id)
        if job.state in TERMINAL_STATES:
            return
        job.state = QUEUED
        job.progress = 0
        job.error_class = "RecoverableComputeError"
        job.error_detail = str(exc)[:300]
        job.external_request_count = 0
        job.save()
        _audit(job, str(exc)[:300])


def _publish(job_id: str, normalized: bytes) -> None:
    with transaction.atomic():
        job = (
            ComputeJob.objects.select_for_update(of=("self",))
            .select_related("input_asset", "output_asset")
            .get(pk=job_id)
        )
        if job.state in TERMINAL_STATES:
            return
        job.state = VERIFYING
        job.progress = 80
        job.save(update_fields=["state", "progress", "updated_at"])
        _audit(job, "Verifying mesh count and output hash before publication.")
        output = validate_gltf(json.loads(normalized))
        source_bytes = read_bytes(str(job.organization_id), job.input_asset.storage_key)
        source_document = json.loads(source_bytes)
        if output.mesh_count != len(source_document.get("meshes") or []) or output.mesh_count < 1:
            raise PermanentComputeError("Verified mesh count does not match the source.")
        digest = sha256_hex(normalized)
        derived = _store_derived(job, normalized)
        if derived.sha256 != digest:
            raise PermanentComputeError("Published hash does not match the verified bytes.")
        gpu = job.preflight.get("gpu") if isinstance(job.preflight, dict) else {}
        job.output_asset = derived
        job.state = SUCCEEDED
        job.progress = 100
        job.error_class = ""
        job.error_detail = ""
        job.estimated_cost_amount = None
        job.actual_cost_amount = ZERO
        job.external_request_count = 0
        job.source_retained = file_exists(str(job.organization_id), job.input_asset.storage_key)
        job.finished_at = timezone.now()
        job.provenance = {
            "operation": job.operation,
            "provider_id": job.provider_id,
            "source_sha256": job.input_asset.sha256,
            "output_sha256": digest,
            "mesh_count": output.mesh_count,
            "reconstruction": False,
            "photogrammetry": False,
            "remote_worker": False,
            "processing_gpu_required_to_view": False,
            "browser_tiles": False,
            "transport": "in-process",
            "production_credentials_sent": False,
            "gpu": gpu,
            "output_provenance": PROVENANCE_MODELLED,
            "source_provenance": PROVENANCE_USER,
        }
        _burn_capability(job)
        job.cleanup = _cleanup(job, published=True)
        job.save()
        _audit(job, "Output published after hash verification. Capacity released.")


def _fail(job_id: str, exc: Exception) -> None:
    with transaction.atomic():
        job = (
            ComputeJob.objects.select_for_update(of=("self",))
            .select_related("input_asset")
            .get(pk=job_id)
        )
        if job.state in TERMINAL_STATES:
            return
        job.state = FAILED
        job.progress = 100
        job.error_class = type(exc).__name__[:64]
        job.error_detail = str(exc)[:300]
        job.output_asset = None
        job.estimated_cost_amount = None
        job.actual_cost_amount = ZERO
        job.external_request_count = 0
        job.finished_at = timezone.now()
        job.source_retained = bool(
            job.input_asset_id
            and job.input_asset.storage_key
            and file_exists(str(job.organization_id), job.input_asset.storage_key)
        )
        job.provenance = {
            "provider_id": job.provider_id,
            "reconstruction": False,
            "remote_worker": False,
            "production_credentials_sent": False,
            "published": False,
        }
        _burn_capability(job)
        job.cleanup = _cleanup(job, published=False)
        job.save()
        _audit(job, "Job failed. Source retained and no output was published.")


def cancel_job(job: ComputeJob) -> ComputeJob:
    with transaction.atomic():
        locked = (
            ComputeJob.objects.select_for_update(of=("self",))
            .select_related("input_asset")
            .get(pk=job.pk)
        )
        if locked.state in TERMINAL_STATES:
            raise JobAlreadyTerminal("This job is already finished.")
        locked.state = CANCELLED
        locked.finished_at = timezone.now()
        locked.external_request_count = 0
        locked.actual_cost_amount = ZERO
        locked.source_retained = bool(
            locked.input_asset_id
            and locked.input_asset.storage_key
            and file_exists(str(locked.organization_id), locked.input_asset.storage_key)
        )
        _burn_capability(locked)
        locked.cleanup = _cleanup(locked, published=False)
        locked.save()
        _audit(locked, "Job cancelled. Source retained and capacity released.")
        return locked


def job_payload(job: ComputeJob) -> dict:
    preflight = job.preflight if isinstance(job.preflight, dict) else {}
    return {
        "id": str(job.id),
        "provider_id": job.provider_id,
        "operation": job.operation,
        "state": job.state,
        "progress": job.progress,
        "attempt": job.attempt,
        "max_attempts": job.max_attempts,
        "error_class": job.error_class,
        "error_detail": job.error_detail,
        "source_retained": job.source_retained,
        "cleanup": job.cleanup,
        "estimated_cost_amount": money(job.estimated_cost_amount),
        "estimated_cost_note": ESTIMATE_NOTE,
        "actual_cost_amount": money(job.actual_cost_amount),
        "actual_cost_note": ACTUAL_NOTE,
        "external_request_count": job.external_request_count,
        "preflight": preflight,
        "provenance": job.provenance,
        "input_sha256": job.input_asset.sha256 if job.input_asset_id else None,
        "output_sha256": job.output_asset.sha256 if job.output_asset_id else None,
        "output_asset_id": str(job.output_asset_id) if job.output_asset_id else None,
        "output_asset_type": job.output_asset.asset_type if job.output_asset_id else None,
        "output_media_type": job.output_asset.media_type if job.output_asset_id else None,
        "output_published": bool(job.output_asset_id and job.state == SUCCEEDED),
        "simulate": job.simulate,
        "created_at": job.created_at.isoformat(),
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
    }


def event_payload(event: ComputeJobEvent) -> dict:
    return {
        "id": str(event.id),
        "state": event.state,
        "message": event.message,
        "created_at": event.created_at.isoformat(),
    }


def dashboard_payload(organization) -> dict:
    jobs = ComputeJob.objects.filter(organization=organization)
    counts = {
        row["state"]: row["n"] for row in jobs.values("state").annotate(n=Count("id"))
    }
    total = jobs.aggregate(total=Sum("actual_cost_amount"))["total"] or ZERO
    requests = jobs.aggregate(total=Sum("external_request_count"))["total"] or 0
    entitled = set(
        ComputeEntitlement.objects.filter(organization=organization).values_list(
            "provider_id", flat=True
        )
    )
    snapshot = host_snapshot()
    return {
        "queue_depth": sum(counts.get(state, 0) for state in QUEUED_STATES),
        "processing": counts.get(RUNNING, 0),
        "by_state": {state: counts.get(state, 0) for state in JOB_STATES},
        "resources": snapshot,
        "estimated_cost": {"amount": None, "currency": None, "note": ESTIMATE_NOTE},
        "actual_cost": {
            "amount": money(total),
            "currency": None,
            "note": ACTUAL_NOTE,
            "external_request_count": int(requests),
        },
        "providers": provider_catalog(entitled_ids=entitled),
        "adapters": adapter_status(),
        "remote_render": {
            "enabled": False,
            "flag": bool(getattr(settings, "GLITTERS_REMOTE_RENDER_ENABLED", False)),
            "reason": _remote_reason(),
        },
        "note": (
            "GPU processing is separate from browser 3D Tiles rendering. "
            "A published fixture stays readable after the processing process exits."
        ),
    }


def _remote_reason() -> str:
    try:
        open_remote_render()
    except RemoteRenderDisabled as exc:
        return str(exc)
    return "Remote rendering did not open."
