"""Shared compute identifiers. This module does not contact a provider."""

from __future__ import annotations

LOCAL_CPU = "local_cpu"
LOCAL_GPU = "local_gpu"
RUNPOD = "runpod"
VAST = "vast"
ENTERPRISE = "enterprise"
MOCK_RENTED_GPU = "mock_rented_gpu"

PROVIDER_IDS = (
    LOCAL_CPU,
    LOCAL_GPU,
    RUNPOD,
    VAST,
    ENTERPRISE,
    MOCK_RENTED_GPU,
)
COMMERCIAL_PROVIDERS = frozenset({RUNPOD, VAST, ENTERPRISE})
PROVIDER_CHOICES = [(item, item) for item in PROVIDER_IDS]

PENDING = "PENDING"
VALIDATING = "VALIDATING"
QUEUED = "QUEUED"
RUNNING = "RUNNING"
VERIFYING = "VERIFYING"
SUCCEEDED = "SUCCEEDED"
FAILED = "FAILED"
CANCELLED = "CANCELLED"
JOB_STATES = (
    PENDING,
    VALIDATING,
    QUEUED,
    RUNNING,
    VERIFYING,
    SUCCEEDED,
    FAILED,
    CANCELLED,
)
TERMINAL_STATES = frozenset({SUCCEEDED, FAILED, CANCELLED})
QUEUED_STATES = frozenset({PENDING, VALIDATING, QUEUED})
STATE_CHOICES = [(item, item) for item in JOB_STATES]

OPERATION_GLTF = "gltf_normalize"
OPERATION_TERRAIN = "publish_terrain_surface"
OPERATION_BUILDINGS = "publish_building_models"
OPERATION_TERRAIN_TILES = "publish_terrain_tiles"
OPERATION_CITY_TILES = "publish_city_tiles"
SURFACE_OPERATIONS = frozenset(
    {OPERATION_TERRAIN, OPERATION_BUILDINGS, OPERATION_TERRAIN_TILES, OPERATION_CITY_TILES}
)
# A succeeded job is reused only when this revision matches. Terrain v1 published
# levels 10-15 only, and Cesium stopped at the empty ancestors.
SURFACE_PRODUCT_REVISIONS = {
    OPERATION_TERRAIN_TILES: "quantized-mesh-pyramid-v2",
}
FIXTURE_TRIANGLE = "triangle-v1"
SIMULATE_FAIL_ONCE = "fail_once"
SIMULATE_FAIL = "fail"

ASSET_SOURCE = "source"
ASSET_DERIVED = "derived"
PROVENANCE_USER = "USER-SUPPLIED"
PROVENANCE_MODELLED = "MODELLED"
PROVENANCE_DOWNLOADED = "DOWNLOADED"

ESTIMATE_NOTE = "No contracted rate card. This is not a quote."
ACTUAL_NOTE = "No rented GPU was used. Recorded actual cost is 0. This is not an invoice."
REMOTE_RENDER_REASON = (
    "Remote rendering is disabled until bandwidth, latency, GPU cost, and security are validated."
)
COMMERCIAL_REASON = (
    "Commercial GPU rental has no live connector. "
    "No instance was provisioned and no billable request was sent."
)


class ComputeError(Exception):
    """A compute job stopped. The original source file is left in place."""

    recoverable = False


class RecoverableComputeError(ComputeError):
    """The same job may try again. This does not contact a rental API."""

    recoverable = True


class PermanentComputeError(ComputeError):
    recoverable = False


class DatasetTooLarge(PermanentComputeError):
    pass


class QuotaExceeded(PermanentComputeError):
    """The organization storage quota or per-file cap refused the write."""


class AssetInUse(PermanentComputeError):
    """A compute job still references this asset."""


class ProviderNotConfigured(PermanentComputeError):
    pass


class ComputeUnavailable(PermanentComputeError):
    pass


class RemoteRenderDisabled(PermanentComputeError):
    pass


class JobAlreadyTerminal(PermanentComputeError):
    pass
