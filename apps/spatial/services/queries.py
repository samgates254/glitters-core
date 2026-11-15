"""Reusable spatial query services for GLITTERS Core.

Adheres to M4.0 ADR-01: Geometry is queried polymorphically from child models
(Building.geom, LandParcel.geom, Road.geom), never assuming a universal geometry
column on the SpatialEntity base model.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple, Union
import uuid

from django.contrib.gis.geos import GEOSGeometry, Polygon
from django.core.exceptions import ValidationError
from django.db import models

from apps.organizations.models import Organization
from apps.spatial.domain import DatasetVersionStatus, EntityType
from apps.spatial.models import (
    AdministrativeBoundary,
    Building,
    ConstructionSite,
    InfrastructureAsset,
    LandParcel,
    Road,
    SpatialEntity,
    SurveyObservation,
    TerrainFeature,
)

logger = logging.getLogger(__name__)


def validate_bounding_box(
    min_lon: float, min_lat: float, max_lon: float, max_lat: float
) -> Polygon:
    """
    Validates coordinate ranges and returns a closed WGS 84 Polygon envelope.
    Raises ValidationError if coordinates are invalid or inverted.
    """
    if not (-180.0 <= min_lon <= 180.0 and -180.0 <= max_lon <= 180.0):
        raise ValidationError(
            f"Longitude values must be between -180 and 180. Received: [{min_lon}, {max_lon}]"
        )
    if not (-90.0 <= min_lat <= 90.0 and -90.0 <= max_lat <= 90.0):
        raise ValidationError(
            f"Latitude values must be between -90 and 90. Received: [{min_lat}, {max_lat}]"
        )
    if min_lon > max_lon:
        raise ValidationError(
            f"min_lon ({min_lon}) cannot be greater than max_lon ({max_lon})."
        )
    if min_lat > max_lat:
        raise ValidationError(
            f"min_lat ({min_lat}) cannot be greater than max_lat ({max_lat})."
        )

    # Construct counter-clockwise bounding polygon
    poly = Polygon.from_bbox((min_lon, min_lat, max_lon, max_lat))
    poly.srid = 4326
    return poly


def query_buildings_in_bbox(
    bbox: Tuple[float, float, float, float],
    organization: Optional[Organization] = None,
    classification: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
) -> Tuple[models.QuerySet[Building], int]:
    """
    Retrieves buildings whose geometry intersects the bounding box envelope.
    Applies publication isolation (excludes INGESTING, FAILED, CANCELLED) and tenant scoping.
    Eagerly loads related pointers to prevent N+1 queries.
    """
    bbox_poly = validate_bounding_box(bbox[0], bbox[1], bbox[2], bbox[3])
    bounded_limit = max(1, min(limit, 1000))
    bounded_offset = max(0, offset)

    qs = (
        Building.objects.for_organization(organization)
        .exclude(
            dataset_version__status__in=[
                DatasetVersionStatus.INGESTING,
                DatasetVersionStatus.FAILED,
                DatasetVersionStatus.CANCELLED,
            ]
        )
        .filter(geom__intersects=bbox_poly)
        .select_related("spatialentity_ptr", "dataset_version__dataset")
        .prefetch_related("external_mappings")
    )

    if classification:
        qs = qs.filter(classification=classification.upper())

    total_count = qs.count()
    results = qs[bounded_offset : bounded_offset + bounded_limit]
    return results, total_count


def query_buildings_intersecting_geometry(
    geometry: Union[str, Dict[str, Any], GEOSGeometry],
    organization: Optional[Organization] = None,
    limit: int = 100,
    offset: int = 0,
) -> Tuple[models.QuerySet[Building], int]:
    """
    Retrieves buildings whose geometry intersects an arbitrary GEOS geometry.
    Validates geometry topology before execution.
    """
    if isinstance(geometry, str) or isinstance(geometry, dict):
        import json
        geom_str = json.dumps(geometry) if isinstance(geometry, dict) else geometry
        try:
            geos_geom = GEOSGeometry(geom_str)
        except Exception as e:
            raise ValidationError(f"Invalid geometry representation: {e}") from e
    elif isinstance(geometry, GEOSGeometry):
        geos_geom = geometry
    else:
        raise ValidationError(f"Unsupported geometry type: {type(geometry)}")

    if not geos_geom.valid:
        raise ValidationError(f"Supplied geometry is topologically invalid: {geos_geom.valid_reason}")

    if geos_geom.srid is None or geos_geom.srid != 4326:
        geos_geom.srid = 4326

    bounded_limit = max(1, min(limit, 1000))
    bounded_offset = max(0, offset)

    qs = (
        Building.objects.for_organization(organization)
        .exclude(
            dataset_version__status__in=[
                DatasetVersionStatus.INGESTING,
                DatasetVersionStatus.FAILED,
                DatasetVersionStatus.CANCELLED,
            ]
        )
        .filter(geom__intersects=geos_geom)
        .select_related("spatialentity_ptr", "dataset_version__dataset")
        .prefetch_related("external_mappings")
    )

    total_count = qs.count()
    results = qs[bounded_offset : bounded_offset + bounded_limit]
    return results, total_count


def get_building_by_urn(
    identifier: str,
    organization: Optional[Organization] = None,
) -> Optional[Building]:
    """
    Retrieves a single building by canonical URN, UUID, or external provider ID.
    Enforces tenant isolation and prefetches mappings to prevent N+1 queries.
    """
    if not identifier:
        return None

    qs = (
        Building.objects.for_organization(organization)
        .select_related("spatialentity_ptr", "dataset_version__dataset")
        .prefetch_related("external_mappings")
    )

    # 1. Exact canonical URN match
    building = qs.filter(canonical_urn=identifier).first()
    if building:
        return building

    # 2. Check UUID
    try:
        parsed_uuid = uuid.UUID(identifier)
        building = qs.filter(id=parsed_uuid).first()
        if building:
            return building
    except (ValueError, TypeError):
        pass

    # 3. Check external provider mappings (e.g. way/123456 or 123456)
    ids_to_try = [identifier]
    if not identifier.startswith("way/") and not identifier.startswith("relation/"):
        ids_to_try.extend([f"way/{identifier}", f"relation/{identifier}"])

    building = qs.filter(
        external_mappings__provider_external_id__in=ids_to_try
    ).first()

    return building


def get_specialized_entity(
    canonical_urn: str,
    organization: Optional[Organization] = None,
) -> Optional[Dict[str, Any]]:
    """
    Retrieves any specialized spatial entity (Building, LandParcel, Road, etc.)
    with its specialized geometry and typed attributes.
    """
    base_entity = (
        SpatialEntity.objects.for_organization(organization)
        .filter(canonical_urn=canonical_urn)
        .first()
    )
    if not base_entity:
        return None

    entity_type = base_entity.entity_type
    specialized_obj: Any = None
    geom_data: Any = None

    if entity_type == EntityType.BUILDING:
        specialized_obj = Building.objects.filter(id=base_entity.id).first()
        geom_data = specialized_obj.geom if specialized_obj else None
    elif entity_type == EntityType.LAND_PARCEL:
        specialized_obj = LandParcel.objects.filter(id=base_entity.id).first()
        geom_data = specialized_obj.geom if specialized_obj else None
    elif entity_type == EntityType.ROAD:
        specialized_obj = Road.objects.filter(id=base_entity.id).first()
        geom_data = specialized_obj.geom if specialized_obj else None
    elif entity_type == EntityType.CONSTRUCTION_SITE:
        specialized_obj = ConstructionSite.objects.filter(id=base_entity.id).first()
        geom_data = specialized_obj.geom if specialized_obj else None
    elif entity_type == EntityType.SURVEY_OBSERVATION:
        specialized_obj = SurveyObservation.objects.filter(id=base_entity.id).first()
        geom_data = specialized_obj.geom if specialized_obj else None
    elif entity_type == EntityType.INFRASTRUCTURE:
        specialized_obj = InfrastructureAsset.objects.filter(id=base_entity.id).first()
        geom_data = specialized_obj.geom if specialized_obj else None
    elif entity_type == EntityType.ADMINISTRATIVE_BOUNDARY:
        specialized_obj = AdministrativeBoundary.objects.filter(id=base_entity.id).first()
        geom_data = specialized_obj.geom if specialized_obj else None
    elif entity_type == EntityType.TERRAIN:
        specialized_obj = TerrainFeature.objects.filter(id=base_entity.id).first()
        geom_data = specialized_obj.geom if specialized_obj else None

    return {
        "base_entity": base_entity,
        "specialized_entity": specialized_obj,
        "entity_type": entity_type,
        "geometry": geom_data,
        "canonical_urn": base_entity.canonical_urn,
    }
