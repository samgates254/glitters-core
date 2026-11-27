"""Organization-scoped compute jobs. Commercial providers are refused here."""

from __future__ import annotations

from django.http import FileResponse
from rest_framework import exceptions, permissions, status, viewsets
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.response import Response

from apps.spatial.godseye_views import _one_organization
from apps.spatial.models import ComputeJob, ComputeJobEvent
from apps.spatial.services.compute.contracts import (
    COMMERCIAL_PROVIDERS,
    LOCAL_CPU,
    ComputeError,
    DatasetTooLarge,
    JobAlreadyTerminal,
    RemoteRenderDisabled,
)
from apps.spatial.services.compute.providers import (
    adapter_status,
    open_remote_render,
    provider_catalog,
)
from apps.spatial.services.compute.runner import (
    cancel_job,
    dashboard_payload,
    event_payload,
    job_payload,
    submit_job,
)
from apps.spatial.services.compute.storage import resolve_compute_file
from apps.spatial.services.request_scope import scope_member_orgs
from apps.spatial.services.safe_paths import UnsafeSpatialPath
from apps.spatial.throttles import SpatialComputeRateThrottle


def _public_error(exc: ComputeError) -> exceptions.APIException:
    if isinstance(exc, DatasetTooLarge):
        api_error = exceptions.ValidationError({"detail": str(exc)})
        return api_error
    if isinstance(exc, JobAlreadyTerminal):
        conflict = exceptions.ValidationError({"detail": str(exc)})
        conflict.status_code = status.HTTP_409_CONFLICT
        return conflict
    api_error = exceptions.ValidationError({"detail": str(exc)})
    return api_error


class ComputeJobViewSet(viewsets.GenericViewSet):
    """Queue and read jobs owned by the caller's organization."""

    permission_classes = [permissions.IsAuthenticated]
    queryset = ComputeJob.objects.all()
    http_method_names = ["get", "post", "head", "options"]

    def get_queryset(self):
        return scope_member_orgs(
            ComputeJob.objects.select_related("input_asset", "output_asset"),
            self.request,
            "organization_id",
        )

    def get_throttles(self):
        if self.action == "create":
            return [SpatialComputeRateThrottle()]
        return super().get_throttles()

    def list(self, request):
        _one_organization(request)
        page = self.paginate_queryset(self.get_queryset().order_by("-created_at"))
        return self.get_paginated_response([job_payload(job) for job in page])

    def retrieve(self, request, pk=None):
        _one_organization(request)
        return Response(job_payload(self.get_object()))

    def create(self, request):
        organization = _one_organization(request)
        body = request.data if isinstance(request.data, dict) else {}
        document = body.get("document")
        if document is not None and not isinstance(document, dict):
            raise exceptions.ValidationError({"document": "A glTF JSON object is required."})
        try:
            job, created = submit_job(
                organization=organization,
                user=request.user,
                provider_id=str(body.get("provider_id") or ""),
                operation=str(body.get("operation") or ""),
                fixture=str(body.get("fixture") or ""),
                document=document if isinstance(document, dict) else None,
                idempotency_key=str(body.get("idempotency_key") or ""),
                simulate=str(body.get("simulate") or ""),
            )
        except ComputeError as exc:
            raise _public_error(exc) from exc
        job = self.get_queryset().get(pk=job.pk)
        payload = job_payload(job)
        if job.provider_id in COMMERCIAL_PROVIDERS:
            return Response(payload, status=status.HTTP_403_FORBIDDEN)
        if job.state == "FAILED" and job.error_class == "ComputeUnavailable":
            payload["fallback_provider_id"] = LOCAL_CPU
            return Response(payload, status=status.HTTP_409_CONFLICT)
        return Response(payload, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        _one_organization(request)
        try:
            cancel_job(self.get_object())
        except JobAlreadyTerminal as exc:
            raise _public_error(exc) from exc
        return Response(job_payload(self.get_queryset().get(pk=pk)))

    @action(detail=True, methods=["get"])
    def output(self, request, pk=None):
        job = self.get_object()
        _one_organization(request)
        if job.state != "SUCCEEDED" or not job.output_asset_id:
            raise exceptions.NotFound("No validated output is published for this job.")
        asset = job.output_asset
        if str(asset.organization_id) != str(job.organization_id):
            raise exceptions.NotFound("No validated output is published for this job.")
        try:
            path = resolve_compute_file(str(job.organization_id), asset.storage_key)
        except UnsafeSpatialPath as exc:
            raise exceptions.NotFound("No validated output is published for this job.") from exc
        handle = path.open("rb")
        response = FileResponse(handle, content_type=asset.media_type)
        response["X-Content-SHA256"] = asset.sha256
        response["Cache-Control"] = "private, no-store"
        response["Content-Disposition"] = 'inline; filename="fixture.gltf"'
        return response

    @action(detail=True, methods=["get"])
    def events(self, request, pk=None):
        job = self.get_object()
        _one_organization(request)
        rows = ComputeJobEvent.objects.filter(
            job=job, organization_id=job.organization_id
        ).order_by("created_at")
        return Response({"events": [event_payload(row) for row in rows]})


@api_view(["GET"])
@permission_classes([permissions.IsAuthenticated])
def compute_dashboard(request):
    organization = _one_organization(request)
    return Response(dashboard_payload(organization))


@api_view(["GET"])
@permission_classes([permissions.IsAuthenticated])
def compute_providers(request):
    organization = _one_organization(request)
    from apps.spatial.models import ComputeEntitlement

    entitled = set(
        ComputeEntitlement.objects.filter(organization=organization).values_list(
            "provider_id", flat=True
        )
    )
    return Response({"providers": provider_catalog(entitled_ids=entitled)})


@api_view(["GET"])
@permission_classes([permissions.IsAuthenticated])
def compute_adapters(request):
    _one_organization(request)
    try:
        open_remote_render()
        remote = {"enabled": True, "reason": ""}
    except RemoteRenderDisabled as exc:
        remote = {"enabled": False, "reason": str(exc)}
    return Response({"adapters": adapter_status(), "remote_render": remote})
