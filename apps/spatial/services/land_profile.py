"""Screening measurements for a user-supplied outline.

The result is not a registered parcel, a professional survey, or a planning approval.
"""

from __future__ import annotations

import json
from decimal import ROUND_HALF_UP, Decimal

from django.contrib.gis.geos import GEOSGeometry, Polygon
from django.core.exceptions import ValidationError
from rest_framework import serializers

from apps.spatial.domain import (
    MAX_LAND_AREA_SQM,
    MAX_LAND_VERTICES,
    MIN_LAND_AREA_SQM,
    SRID_WGS84,
    SURVEY_DISCLAIMER,
    ProvenanceClass,
)
from apps.spatial.services.crs import get_optimal_kenya_utm_srid, transform_to_optimal_utm
from apps.spatial.services.validation import validate_spatial_geometry


class SpatialInputError(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def _quantize_metric(value: float) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _quantize_coord(value: float) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)


def _zone_label(epsg: int) -> str:
    if 32601 <= epsg <= 32660:
        return f"{epsg - 32600}N"
    if 32701 <= epsg <= 32760:
        return f"{epsg - 32700}S"
    return str(epsg)


def _vertex_count(polygon: Polygon) -> int:
    return sum(max(len(ring) - 1, 0) for ring in polygon.coords)


def measure_polygon(payload) -> dict:
    """Return model fields for one WGS 84 polygon."""

    if isinstance(payload, dict) and payload.get("type") == "Feature":
        payload = payload.get("geometry")
    if not isinstance(payload, dict):
        raise SpatialInputError("Send one GeoJSON Polygon.")
    if payload.get("type") != "Polygon":
        raise SpatialInputError(
            "Send one GeoJSON Polygon. A drawn outline is not a registered parcel."
        )
    if "crs" in payload:
        raise SpatialInputError("Send WGS 84 longitude and latitude without a CRS member.")
    try:
        geom = GEOSGeometry(json.dumps(payload), srid=SRID_WGS84)
    except Exception as exc:
        raise SpatialInputError("The polygon could not be read.") from exc
    if not isinstance(geom, Polygon):
        raise SpatialInputError("Send one GeoJSON Polygon.")
    if geom.hasz:
        raise SpatialInputError("Three-dimensional coordinates are not accepted.")
    try:
        validate_spatial_geometry(geom)
    except ValidationError as exc:
        parts = exc.messages if hasattr(exc, "messages") else [str(exc)]
        raise SpatialInputError("; ".join(str(item) for item in parts)) from None
    vertices = _vertex_count(geom)
    if vertices > MAX_LAND_VERTICES:
        raise SpatialInputError(f"Use at most {MAX_LAND_VERTICES} vertices.")
    min_lon, min_lat, max_lon, max_lat = geom.extent
    if min_lat < 0 < max_lat:
        raise SpatialInputError("The area crosses the equator. Measure each side separately.")
    west_epsg = get_optimal_kenya_utm_srid(min_lon, geom.centroid.y)
    east_epsg = get_optimal_kenya_utm_srid(max_lon, geom.centroid.y)
    if west_epsg != east_epsg:
        raise SpatialInputError("The area crosses a UTM zone. Split it before measuring.")
    try:
        projected = transform_to_optimal_utm(geom)
    except Exception as exc:
        raise SpatialInputError("The outline could not be projected.") from exc
    area = float(projected.area)
    perimeter = float(projected.length)
    if area < MIN_LAND_AREA_SQM:
        raise SpatialInputError("The outline is smaller than 10 square metres.")
    if area > MAX_LAND_AREA_SQM:
        raise SpatialInputError("The outline is larger than 20 square kilometres.")
    centroid = geom.centroid
    return {
        "geometry": geom,
        "vertex_count": vertices,
        "area_sqm": _quantize_metric(area),
        "perimeter_m": _quantize_metric(perimeter),
        "centroid_longitude": _quantize_coord(centroid.x),
        "centroid_latitude": _quantize_coord(centroid.y),
        "bbox_min_longitude": _quantize_coord(min_lon),
        "bbox_min_latitude": _quantize_coord(min_lat),
        "bbox_max_longitude": _quantize_coord(max_lon),
        "bbox_max_latitude": _quantize_coord(max_lat),
        "metric_epsg": west_epsg,
        "utm_zone": _zone_label(west_epsg),
        "geometry_evidence": ProvenanceClass.USER_SUPPLIED,
        "measurement_evidence": ProvenanceClass.MODELLED,
        "measurement_note": SURVEY_DISCLAIMER,
    }


def measurement_error(exc: SpatialInputError) -> serializers.ValidationError:
    return serializers.ValidationError({"geometry": exc.message})
