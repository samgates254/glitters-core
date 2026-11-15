"""Geometry sanity checks, topological repair helpers, and acyclic lineage validation."""

from __future__ import annotations

from __future__ import annotations

import math
from django.contrib.gis.geos import GEOSGeometry, MultiPolygon, Polygon
from django.core.exceptions import ValidationError

from apps.spatial.domain import (
    COORDINATES_OUT_OF_BOUNDS,
    COORDINATES_OUTSIDE_ENVELOPE,
    EMPTY_GEOMETRY,
    INVALID_GEOMETRY,
    INVALID_SRID,
    LINEAGE_CYCLE_DETECTED,
    MOMBASA_ENVELOPE_MAX_LAT,
    MOMBASA_ENVELOPE_MAX_LON,
    MOMBASA_ENVELOPE_MIN_LAT,
    MOMBASA_ENVELOPE_MIN_LON,
    PROJECTION_FAILED,
    SRID_WGS84,
)
from apps.spatial.services.crs import transform_to_optimal_utm


def validate_spatial_geometry(geom: GEOSGeometry, expected_dim: int = 2) -> None:
    """Validates geometry integrity, SRID, planetary coordinate bounds, and validity."""
    if geom is None or geom.empty:
        raise ValidationError(EMPTY_GEOMETRY)

    if geom.srid != SRID_WGS84:
        raise ValidationError(INVALID_SRID)

    # Check bounds
    extent = geom.extent  # (xmin, ymin, xmax, ymax)
    if (
        extent[0] < -180.0
        or extent[2] > 180.0
        or extent[1] < -90.0
        or extent[3] > 90.0
    ):
        raise ValidationError(COORDINATES_OUT_OF_BOUNDS)

    if not geom.valid:
        raise ValidationError(f"{INVALID_GEOMETRY} Reason: {geom.valid_reason}")

    if expected_dim == 3 and not geom.hasz:
        raise ValidationError("3D Geometry (PointZ) requires Z coordinate.")


def validate_mombasa_geographic_envelope(geom: GEOSGeometry) -> None:
    """Validates that a geometry's coordinate envelope is strictly inside Mombasa operational bounds."""
    extent = geom.extent
    if (
        extent[0] < MOMBASA_ENVELOPE_MIN_LON
        or extent[2] > MOMBASA_ENVELOPE_MAX_LON
        or extent[1] < MOMBASA_ENVELOPE_MIN_LAT
        or extent[3] > MOMBASA_ENVELOPE_MAX_LAT
    ):
        raise ValidationError(
            f"{COORDINATES_OUTSIDE_ENVELOPE} Extent {extent} outside Mombasa [{MOMBASA_ENVELOPE_MIN_LON}, {MOMBASA_ENVELOPE_MIN_LAT}, {MOMBASA_ENVELOPE_MAX_LON}, {MOMBASA_ENVELOPE_MAX_LAT}]."
        )


def validate_projection_suitability(geom: GEOSGeometry) -> None:
    """Verifies that geometry transforms cleanly to local UTM without coordinate divergence or collapse."""
    try:
        utm_geom = transform_to_optimal_utm(geom)
        if utm_geom.empty:
            raise ValidationError(f"{PROJECTION_FAILED}: Transformed geometry is empty.")
        area = utm_geom.area
        if math.isnan(area) or math.isinf(area) or area <= 0.0:
            raise ValidationError(f"{PROJECTION_FAILED}: Computed planar area is invalid ({area}).")
    except Exception as exc:
        if isinstance(exc, ValidationError):
            raise
        raise ValidationError(f"{PROJECTION_FAILED}: {exc}") from exc


def try_repair_geometry(geom: GEOSGeometry) -> tuple[GEOSGeometry, bool, str]:
    """
    Attempts to heal an invalid or non-MultiPolygon geometry into a valid MultiPolygon.
    Returns (healed_geom, was_repaired, repair_details).
    """
    was_repaired = False
    details = []

    current_geom = geom
    # Auto-upgrade Polygon to MultiPolygon
    if isinstance(current_geom, Polygon):
        current_geom = MultiPolygon([current_geom], srid=SRID_WGS84)
        was_repaired = True
        details.append("Promoted Polygon to MultiPolygon")

    if not current_geom.valid:
        try:
            repaired = current_geom.make_valid()
            if repaired and not repaired.empty:
                if isinstance(repaired, MultiPolygon):
                    current_geom = repaired
                    was_repaired = True
                    details.append(f"make_valid healed topology: {repaired.valid_reason}")
                elif isinstance(repaired, Polygon):
                    current_geom = MultiPolygon([repaired], srid=SRID_WGS84)
                    was_repaired = True
                    details.append("make_valid returned Polygon; promoted to MultiPolygon")
                elif repaired.geom_type == "GeometryCollection":
                    # Extract polygon parts only
                    polygons = [part for part in repaired if isinstance(part, Polygon)]
                    if polygons:
                        current_geom = MultiPolygon(polygons, srid=SRID_WGS84)
                        was_repaired = True
                        details.append(f"Extracted {len(polygons)} polygons from GeometryCollection")
                    else:
                        details.append("GeometryCollection contained no polygons")
        except Exception as exc:
            details.append(f"make_valid failed: {exc}")

    return current_geom, was_repaired, "; ".join(details)



def check_acyclic_lineage(predecessor_urn: str, successor_urn: str) -> None:
    """
    Asserts that adding a lineage edge from predecessor to successor does not introduce a cycle.
    Raises ValidationError if predecessor == successor or if predecessor is reachable from successor.
    """
    if predecessor_urn == successor_urn:
        raise ValidationError(LINEAGE_CYCLE_DETECTED)

    # Import locally to avoid circular dependencies
    from apps.spatial.models import EntityLineage

    # Check transitive descendants of successor using BFS
    visited = set()
    queue = [successor_urn]

    while queue:
        current = queue.pop(0)
        if current == predecessor_urn:
            raise ValidationError(LINEAGE_CYCLE_DETECTED)
        if current in visited:
            continue
        visited.add(current)
        # Fetch direct descendants
        children = EntityLineage.objects.filter(predecessor_urn=current).values_list(
            "successor_urn", flat=True
        )
        for child in children:
            if child not in visited:
                queue.append(child)
