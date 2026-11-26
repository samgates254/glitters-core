"""Rate limits for spatial compute endpoints."""

from rest_framework.throttling import UserRateThrottle


class SpatialComputeRateThrottle(UserRateThrottle):
    """Limit tileset generation, raster jobs, and other CPU-heavy spatial writes."""

    scope = "spatial_compute"
    rate = "30/minute"
