"""Organization asset registry. Files stay on this machine."""

from __future__ import annotations

from django.http import FileResponse
from rest_framework import exceptions, permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.spatial.godseye_views import _one_organization
from apps.spatial.models import GeospatialAsset, GeospatialAssetEvent
from apps.spatial.services.asset_registry import (
    asset_payload,
    log_event,
    prepare_upload,
    resolve_owners,
    soft_delete,
    store_bytes,
    usage_payload,
)
from apps.spatial.services.compute.contracts import AssetInUse, ComputeError, QuotaExceeded
from apps.spatial.services.compute.storage import resolve_compute_file
from apps.spatial.services.request_scope import scope_member_orgs
from apps.spatial.services.safe_paths import UnsafeSpatialPath


def _public_error(exc: ComputeError) -> exceptions.APIException:
    if isinstance(exc, AssetInUse):
        conflict = exceptions.ValidationError({"detail": str(exc)})
        conflict.status_code = status.HTTP_409_CONFLICT
        return conflict
    if isinstance(exc, QuotaExceeded):
        return exceptions.ValidationError({"detail": str(exc)})
    return exceptions.ValidationError({"detail": str(exc)})


class GeospatialAssetViewSet(viewsets.GenericViewSet):
    """Upload, list, and stream files owned by the caller's organization."""

    permission_classes = [permissions.IsAuthenticated]
    queryset = GeospatialAsset.objects.all()
    http_method_names = ["get", "post", "delete", "head", "options"]

    def get_queryset(self):
        queryset = scope_member_orgs(GeospatialAsset.objects.all(), self.request, "organization_id")
        if self.action in {"list", "retrieve", "content", "destroy"}:
            queryset = queryset.filter(deleted_at__isnull=True)
        return queryset

    def list(self, request):
        _one_organization(request)
        page = self.paginate_queryset(self.get_queryset().order_by("-created_at"))
        return self.get_paginated_response([asset_payload(asset) for asset in page])

    def retrieve(self, request, pk=None):
        _one_organization(request)
        return Response(asset_payload(self.get_object()))

    def create(self, request):
        organization = _one_organization(request)
        upload = request.FILES.get("file")
        if upload is None:
            raise exceptions.ValidationError({"file": "A file is required."})
        payload = upload.read()
        asset_type = str(request.data.get("asset_type") or "")
        try:
            prepared = prepare_upload(asset_type, payload)
            scene, project, parent = resolve_owners(
                organization=organization,
                scene_id=str(request.data.get("scene_id") or ""),
                project_id=str(request.data.get("project_id") or ""),
                parent_id=str(request.data.get("replaces") or ""),
            )
            asset = store_bytes(
                organization=organization,
                user=request.user,
                payload=payload,
                kind=prepared["kind"],
                media_type=prepared["media_type"],
                provenance=prepared["provenance"],
                asset_type=asset_type,
                status=prepared["status"],
                suffix=prepared["suffix"],
                license_identifier=prepared["license_identifier"],
                attribution=prepared["attribution"],
                source_note=prepared["source_note"],
                scene=scene,
                analysis_project=project,
                parent=parent,
            )
        except ComputeError as exc:
            raise _public_error(exc) from exc
        return Response(asset_payload(asset), status=status.HTTP_201_CREATED)

    def destroy(self, request, pk=None):
        _one_organization(request)
        asset = self.get_object()
        try:
            soft_delete(asset)
        except ComputeError as exc:
            raise _public_error(exc) from exc
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=["get"])
    def content(self, request, pk=None):
        asset = self.get_object()
        _one_organization(request)
        if asset.deleted_at is not None or not asset.storage_key:
            raise exceptions.NotFound("That asset is not available.")
        try:
            path = resolve_compute_file(str(asset.organization_id), asset.storage_key)
        except UnsafeSpatialPath as exc:
            raise exceptions.NotFound("That asset is not available.") from exc
        handle = path.open("rb")
        response = FileResponse(handle, content_type=asset.media_type or "application/octet-stream")
        response["X-Content-SHA256"] = asset.sha256
        response["Cache-Control"] = "private, no-store"
        response["Content-Disposition"] = 'inline; filename="asset"'
        log_event(asset, "downloaded", f"downloaded {asset.asset_type} {asset.sha256[:12]}")
        return response

    @action(detail=False, methods=["get"])
    def usage(self, request):
        organization = _one_organization(request)
        return Response(usage_payload(organization))

    @action(detail=False, methods=["get"])
    def audit(self, request):
        organization = _one_organization(request)
        events = GeospatialAssetEvent.objects.filter(organization=organization)
        rows = events.order_by("-created_at")[:50]
        return Response(
            {
                "events": [
                    {
                        "id": str(row.id),
                        "asset_id": str(row.asset_id),
                        "action": row.action,
                        "message": row.message,
                        "created_at": row.created_at.isoformat(),
                    }
                    for row in rows
                ]
            }
        )
