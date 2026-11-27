"""Organization files on local disk. No paid object store is used."""

from __future__ import annotations

import json

from django.conf import settings
from django.db.models import Count, Q, Sum
from django.utils import timezone

from apps.spatial.models import (
    ComputeJob,
    GeospatialAsset,
    GeospatialAssetEvent,
    GeospatialScene,
    SpatialAnalysisProject,
)
from apps.spatial.services.asset_validation import _bundle_rows, validate_cog, validate_glb
from apps.spatial.services.city_models import validate_city_document
from apps.spatial.services.compute.contracts import (
    PROVENANCE_DOWNLOADED,
    PROVENANCE_MODELLED,
    PROVENANCE_USER,
    AssetInUse,
    ComputeError,
    PermanentComputeError,
    QuotaExceeded,
)
from apps.spatial.services.compute.gltf_job import sha256_hex, validate_gltf
from apps.spatial.services.compute.storage import (
    read_bytes,
    remove_file,
    safe_relative,
    write_bytes,
)
from apps.spatial.services.safe_paths import UnsafeSpatialPath
from apps.spatial.services.terrain import parse_aaigrid
from apps.spatial.services.terrain_surface import validate_height_grid

RETENTION_NOTE = (
    "Deleted files stay on this machine until an operator removes them. "
    "They no longer count toward the quota."
)
SUFFIXES = {
    "gltf": ".gltf",
    "glb": ".glb",
    "dem": ".asc",
    "cog": ".tif",
    "vector": ".geojson",
    "terrain": ".json",
    "3dtiles": ".json",
    "report": ".json",
}


def quota_bytes() -> int:
    return int(getattr(settings, "GLITTERS_ASSET_QUOTA_BYTES", 64_000_000))


def max_file_bytes() -> int:
    return int(getattr(settings, "GLITTERS_ASSET_MAX_BYTES", 8_000_000))


def active_assets(organization):
    return GeospatialAsset.objects.filter(organization=organization, deleted_at__isnull=True)


def used_bytes(organization) -> int:
    total = active_assets(organization).aggregate(total=Sum("byte_size"))["total"]
    return int(total or 0)


def _quota_message() -> str:
    return (
        "Organization storage quota was reached. The file was not stored. "
        f"Quota is {quota_bytes()} bytes on this machine."
    )


def assert_room(organization, extra: int) -> None:
    if extra < 0:
        raise PermanentComputeError("A negative file size was refused.")
    if extra > max_file_bytes():
        raise QuotaExceeded(
            f"File exceeds the {max_file_bytes()} byte asset limit. It was not stored."
        )
    if used_bytes(organization) + extra > quota_bytes():
        raise QuotaExceeded(_quota_message())


def assert_bundle_room(organization, sizes: list[int]) -> None:
    """Each bundle file stays under the file cap. The sum stays under the quota."""

    if not sizes:
        raise PermanentComputeError("Empty files are not stored.")
    limit = max_file_bytes()
    total = 0
    for size in sizes:
        if size <= 0:
            raise PermanentComputeError("Empty files are not stored.")
        if size > limit:
            raise QuotaExceeded(f"File exceeds the {limit} byte asset limit. It was not stored.")
        total += size
    if used_bytes(organization) + total > quota_bytes():
        raise QuotaExceeded(_quota_message())


def usage_payload(organization) -> dict:
    rows = active_assets(organization).values("asset_type").annotate(
        count=Count("id"),
        bytes=Sum("byte_size"),
    )
    by_type = {
        row["asset_type"]: {"count": row["count"], "bytes": int(row["bytes"] or 0)} for row in rows
    }
    deleted = GeospatialAsset.objects.filter(organization=organization, deleted_at__isnull=False)
    deleted_bytes = deleted.aggregate(total=Sum("byte_size"))["total"]
    used = used_bytes(organization)
    quota = quota_bytes()
    return {
        "used_bytes": used,
        "quota_bytes": quota,
        "remaining_bytes": max(0, quota - used),
        "by_type": by_type,
        "deleted_count": deleted.count(),
        "deleted_bytes": int(deleted_bytes or 0),
        "retention": RETENTION_NOTE,
        "storage": "local-disk",
        "paid_infrastructure": False,
    }


def log_event(asset: GeospatialAsset, action: str, message: str) -> None:
    GeospatialAssetEvent.objects.create(
        asset=asset,
        organization_id=asset.organization_id,
        action=action,
        message=message[:300],
    )


def asset_payload(asset: GeospatialAsset) -> dict:
    return {
        "id": str(asset.id),
        "asset_type": asset.asset_type,
        "kind": asset.kind,
        "status": asset.status,
        "version": asset.version,
        "media_type": asset.media_type,
        "byte_size": asset.byte_size,
        "sha256": asset.sha256,
        "provenance": asset.provenance,
        "license_identifier": asset.license_identifier,
        "attribution": asset.attribution,
        "source_note": asset.source_note,
        "scene_id": str(asset.scene_id) if asset.scene_id else None,
        "project_id": str(asset.analysis_project_id) if asset.analysis_project_id else None,
        "parent_id": str(asset.parent_id) if asset.parent_id else None,
        "deleted_at": asset.deleted_at.isoformat() if asset.deleted_at else None,
        "created_at": asset.created_at.isoformat(),
    }


def _json_object(payload: bytes) -> dict:
    try:
        document = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise PermanentComputeError("The file is not JSON.") from exc
    if not isinstance(document, dict):
        raise PermanentComputeError("A JSON object is required.")
    return document


def _vector_document(payload: bytes) -> None:
    document = _json_object(payload)
    if document.get("format") == "glitters-city-models-v1":
        validate_city_document(document)
        return
    features = document.get("features")
    if document.get("type") != "FeatureCollection" or not isinstance(features, list):
        raise PermanentComputeError("A GeoJSON FeatureCollection is required.")
    if not features:
        raise PermanentComputeError("The vector extract has no features.")


def prepare_upload(asset_type: str, payload: bytes) -> dict:
    """Validate one upload. Invalid files are not stored."""

    if asset_type == "pointcloud" or payload.startswith(b"LASF"):
        raise PermanentComputeError(
            "Point-cloud decoding is not available. The file was not stored."
        )
    if asset_type == "terrain":
        document = _json_object(payload)
        validate_height_grid(document)
        return {
            "kind": "derived",
            "media_type": "application/json",
            "provenance": PROVENANCE_USER,
            "status": "published",
            "suffix": ".json",
            "license_identifier": str(document.get("license") or "")[:80],
            "attribution": str(document.get("attribution") or "")[:240],
            "source_note": str(document.get("limitation") or "")[:400],
        }
    if asset_type == "vector":
        _vector_document(payload)
        return {
            "kind": "derived",
            "media_type": "application/geo+json",
            "provenance": PROVENANCE_USER,
            "status": "published",
            "suffix": ".geojson",
            "license_identifier": "",
            "attribution": "",
            "source_note": "Validated vector extract. Not a title or a survey.",
        }
    if asset_type == "gltf":
        document = _json_object(payload)
        validate_gltf(document)
        return {
            "kind": "source",
            "media_type": "model/gltf+json",
            "provenance": PROVENANCE_USER,
            "status": "published",
            "suffix": ".gltf",
            "license_identifier": "",
            "attribution": "",
            "source_note": "Structural glTF checks passed. This is not a reconstruction.",
        }
    if asset_type == "glb":
        validate_glb(payload)
        return {
            "kind": "source",
            "media_type": "model/gltf-binary",
            "provenance": PROVENANCE_USER,
            "status": "stored",
            "suffix": ".glb",
            "license_identifier": "",
            "attribution": "",
            "source_note": (
                "GLB buffers, accessors, and transforms were checked. This is not photogrammetry."
            ),
        }
    if asset_type == "dem":
        try:
            parse_aaigrid(payload.decode("utf-8"))
        except Exception as exc:
            raise PermanentComputeError("The DEM text could not be parsed.") from exc
        return {
            "kind": "source",
            "media_type": "text/plain",
            "provenance": PROVENANCE_USER,
            "status": "stored",
            "suffix": ".asc",
            "license_identifier": "",
            "attribution": "",
            "source_note": "AAIGrid header parsed. This upload was not certified as a survey.",
        }
    if asset_type == "cog":
        validate_cog(payload)
        return {
            "kind": "source",
            "media_type": "image/tiff",
            "provenance": PROVENANCE_USER,
            "status": "stored",
            "suffix": ".tif",
            "license_identifier": "",
            "attribution": "",
            "source_note": (
                "Tiled raster structure, CRS, and one pixel window were checked. "
                "This is not a survey."
            ),
        }
    if asset_type == "3dtiles":
        document = _json_object(payload)
        asset_block = document.get("asset")
        version = asset_block.get("version") if isinstance(asset_block, dict) else None
        if not isinstance(version, str) or not version:
            raise PermanentComputeError("3D Tiles asset.version is required.")
        root = document.get("root")
        if not isinstance(root, dict) or "boundingVolume" not in root:
            raise PermanentComputeError("3D Tiles root.boundingVolume is required.")
        content = root.get("content")
        uri = content.get("uri") if isinstance(content, dict) else ""
        if isinstance(uri, str):
            external = "://" in uri or uri.startswith(("/", "\\")) or ".." in uri.split("/")
            if external:
                raise PermanentComputeError("A 3D Tiles content URI is not allowed.")
        if uri or root.get("children"):
            raise PermanentComputeError(
                "3D Tiles dependencies are not in this single-file upload. "
                "The tileset was not stored."
            )
        return {
            "kind": "source",
            "media_type": "application/json",
            "provenance": PROVENANCE_USER,
            "status": "stored",
            "suffix": ".json",
            "license_identifier": "",
            "attribution": "",
            "source_note": "3D Tiles JSON was stored. It is not a generated city.",
        }
    if asset_type == "report":
        document = _json_object(payload)
        title = document.get("title")
        if not isinstance(title, str) or not title.strip():
            raise PermanentComputeError("A report needs a title. The file was not stored.")
        return {
            "kind": "derived",
            "media_type": "application/json",
            "provenance": PROVENANCE_USER,
            "status": "published",
            "suffix": ".json",
            "license_identifier": "",
            "attribution": "",
            "source_note": "Report text was stored. It is not a survey certificate.",
        }
    raise PermanentComputeError("That asset type is not accepted.")


def resolve_owners(*, organization, scene_id: str, project_id: str, parent_id: str):
    scene = None
    project = None
    parent = None
    if scene_id:
        scene = GeospatialScene.objects.filter(pk=scene_id, organization=organization).first()
        if scene is None:
            raise PermanentComputeError("That scene is not in this organization.")
    if project_id:
        project = SpatialAnalysisProject.objects.filter(
            pk=project_id, organization=organization
        ).first()
        if project is None:
            raise PermanentComputeError("That project is not in this organization.")
    if parent_id:
        parent = GeospatialAsset.objects.filter(
            pk=parent_id, organization=organization, deleted_at__isnull=True
        ).first()
        if parent is None:
            raise PermanentComputeError("The previous version is not in this organization.")
    return scene, project, parent


def store_bytes(
    *,
    organization,
    user,
    payload: bytes,
    kind: str,
    media_type: str,
    provenance: str,
    asset_type: str,
    status: str,
    suffix: str,
    license_identifier: str = "",
    attribution: str = "",
    source_note: str = "",
    scene=None,
    analysis_project=None,
    parent=None,
) -> GeospatialAsset:
    if not isinstance(payload, (bytes, bytearray)) or len(payload) == 0:
        raise PermanentComputeError("Empty files are not stored.")
    payload = bytes(payload)
    assert_room(organization, len(payload))
    version = 1
    if parent is not None:
        version = int(parent.version) + 1
    if provenance not in {PROVENANCE_USER, PROVENANCE_MODELLED, PROVENANCE_DOWNLOADED}:
        raise PermanentComputeError("Unknown provenance.")
    if suffix not in set(SUFFIXES.values()):
        raise PermanentComputeError("That file suffix is not accepted.")
    asset = GeospatialAsset.objects.create(
        organization=organization,
        created_by=user,
        kind=kind,
        media_type=media_type,
        byte_size=len(payload),
        sha256=sha256_hex(payload),
        storage_key="",
        provenance=provenance,
        asset_type=asset_type,
        status=status,
        version=version,
        parent=parent,
        scene=scene,
        analysis_project=analysis_project,
        license_identifier=license_identifier[:80],
        attribution=attribution[:240],
        source_note=source_note[:400],
    )
    key = f"{organization.id}/{asset.id}{suffix}"
    try:
        write_bytes(str(organization.id), key, payload)
        readback = read_bytes(str(organization.id), key)
    except (OSError, UnsafeSpatialPath) as exc:
        remove_file(str(organization.id), key)
        asset.delete()
        raise PermanentComputeError("The file could not be stored on this machine.") from exc
    if sha256_hex(readback) != asset.sha256 or readback != payload:
        remove_file(str(organization.id), key)
        asset.delete()
        raise PermanentComputeError("Stored bytes failed the read-back check.")
    asset.storage_key = key
    asset.save(update_fields=["storage_key"])
    action = "published" if status == "published" else "stored"
    log_event(asset, action, f"{action} {asset_type} {len(payload)} bytes {asset.sha256[:12]}")
    return asset


def _normalize_bundle(files: dict[str, bytes]) -> dict[str, bytes]:
    if not isinstance(files, dict) or not files:
        raise PermanentComputeError("Empty files are not stored.")
    normalized: dict[str, bytes] = {}
    try:
        for path, payload in files.items():
            safe = safe_relative(str(path))
            if safe in normalized:
                raise PermanentComputeError("A bundle path is not safe.")
            if not isinstance(payload, (bytes, bytearray)) or len(payload) == 0:
                raise PermanentComputeError("Empty files are not stored.")
            normalized[safe] = bytes(payload)
    except UnsafeSpatialPath as exc:
        raise PermanentComputeError("A bundle path is not safe.") from exc
    _bundle_rows(normalized)
    return normalized


def store_bundle(
    *,
    organization,
    user,
    files: dict[str, bytes],
    kind: str,
    media_type: str,
    provenance: str,
    asset_type: str,
    status: str,
    license_identifier: str = "",
    attribution: str = "",
    source_note: str = "",
    scene=None,
    analysis_project=None,
    parent=None,
) -> GeospatialAsset:
    """Store every file under one organization prefix. The manifest hash is the asset hash."""

    normalized = _normalize_bundle(files)
    manifest = normalized["manifest.json"]
    assert_bundle_room(organization, [len(payload) for payload in normalized.values()])
    version = 1
    if parent is not None:
        version = int(parent.version) + 1
    if provenance not in {PROVENANCE_USER, PROVENANCE_MODELLED, PROVENANCE_DOWNLOADED}:
        raise PermanentComputeError("Unknown provenance.")
    total = sum(len(payload) for payload in normalized.values())
    asset = GeospatialAsset.objects.create(
        organization=organization,
        created_by=user,
        kind=kind,
        media_type=media_type,
        byte_size=total,
        sha256=sha256_hex(manifest),
        storage_key="",
        provenance=provenance,
        asset_type=asset_type,
        status=status,
        version=version,
        parent=parent,
        scene=scene,
        analysis_project=analysis_project,
        license_identifier=license_identifier[:80],
        attribution=attribution[:240],
        source_note=source_note[:400],
    )
    org_id = str(organization.id)
    written: list[str] = []
    try:
        for path, payload in normalized.items():
            key = f"{org_id}/{asset.id}/{path}"
            if len(key) > 300:
                raise PermanentComputeError("A bundle path is too long.")
            write_bytes(org_id, key, payload)
            written.append(key)
            readback = read_bytes(org_id, key)
            if readback != payload:
                raise PermanentComputeError("Stored bytes failed the read-back check.")
        asset.storage_key = f"{org_id}/{asset.id}/manifest.json"
        asset.save(update_fields=["storage_key"])
    except ComputeError:
        for key in written:
            remove_file(org_id, key)
        asset.delete()
        raise
    except (OSError, UnsafeSpatialPath) as exc:
        for key in written:
            remove_file(org_id, key)
        asset.delete()
        raise PermanentComputeError("The file could not be stored on this machine.") from exc
    action = "published" if status == "published" else "stored"
    log_event(asset, action, f"{action} {asset_type} bundle {total} bytes {asset.sha256[:12]}")
    return asset


def soft_delete(asset: GeospatialAsset) -> GeospatialAsset:
    if asset.deleted_at is not None:
        raise PermanentComputeError("This asset is already deleted.")
    referenced = ComputeJob.objects.filter(Q(input_asset=asset) | Q(output_asset=asset)).exists()
    if referenced:
        raise AssetInUse("This asset is still referenced by a compute job.")
    asset.status = "deleted"
    asset.deleted_at = timezone.now()
    asset.save(update_fields=["status", "deleted_at"])
    log_event(asset, "deleted", "Soft-deleted. The file is retained and no longer counts.")
    return asset
