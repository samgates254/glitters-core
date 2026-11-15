"""Unit tests for spatial ingestion parser, classification mapper, and quality gates."""

import json
from decimal import Decimal
import pytest
from django.contrib.gis.geos import GEOSGeometry, MultiPolygon, Polygon
from django.core.exceptions import ValidationError

from apps.spatial.domain import (
    BuildingClassification,
    MOMBASA_ENVELOPE_MAX_LAT,
    MOMBASA_ENVELOPE_MAX_LON,
    MOMBASA_ENVELOPE_MIN_LAT,
    MOMBASA_ENVELOPE_MIN_LON,
    ReconciliationDisposition,
    SRID_WGS84,
    map_osm_building_classification,
)
from apps.spatial.services.ingestion import IngestionResult, SpatialIngestionEngine
from apps.spatial.services.validation import (
    try_repair_geometry,
    validate_mombasa_geographic_envelope,
    validate_projection_suitability,
)


def test_map_osm_building_classification():
    # Residential
    assert map_osm_building_classification({"building": "residential"}) == BuildingClassification.RESIDENTIAL
    assert map_osm_building_classification({"building": "house"}) == BuildingClassification.RESIDENTIAL
    assert map_osm_building_classification({"building": "apartments"}) == BuildingClassification.RESIDENTIAL

    # Commercial
    assert map_osm_building_classification({"building": "commercial"}) == BuildingClassification.COMMERCIAL
    assert map_osm_building_classification({"building": "office"}) == BuildingClassification.COMMERCIAL
    assert map_osm_building_classification({"building": "yes", "shop": "supermarket"}) == BuildingClassification.COMMERCIAL
    assert map_osm_building_classification({"building": "yes", "office": "lawyer"}) == BuildingClassification.COMMERCIAL

    # Industrial
    assert map_osm_building_classification({"building": "industrial"}) == BuildingClassification.INDUSTRIAL
    assert map_osm_building_classification({"building": "warehouse"}) == BuildingClassification.INDUSTRIAL

    # Mixed Use
    assert map_osm_building_classification({"building": "mixed_use"}) == BuildingClassification.MIXED_USE

    # Institutional
    assert map_osm_building_classification({"building": "school"}) == BuildingClassification.INSTITUTIONAL
    assert map_osm_building_classification({"building": "hospital"}) == BuildingClassification.INSTITUTIONAL
    assert map_osm_building_classification({"building": "yes", "amenity": "university"}) == BuildingClassification.INSTITUTIONAL

    # Civic
    assert map_osm_building_classification({"building": "civic"}) == BuildingClassification.CIVIC
    assert map_osm_building_classification({"building": "government"}) == BuildingClassification.CIVIC
    assert map_osm_building_classification({"building": "yes", "amenity": "police"}) == BuildingClassification.CIVIC

    # Fallback to Other
    assert map_osm_building_classification({"building": "yes"}) == BuildingClassification.OTHER
    assert map_osm_building_classification({"building": "roof"}) == BuildingClassification.OTHER
    assert map_osm_building_classification({"building": "unrecognized_xyz"}) == BuildingClassification.OTHER
    assert map_osm_building_classification({}) == BuildingClassification.OTHER


def test_validate_mombasa_geographic_envelope():
    # Within Mombasa (Nyali area: 39.70, -4.04)
    valid_poly = Polygon(
        ((39.69, -4.05), (39.71, -4.05), (39.71, -4.03), (39.69, -4.03), (39.69, -4.05)),
        srid=SRID_WGS84,
    )
    validate_mombasa_geographic_envelope(valid_poly)  # Should not raise

    # Outside Mombasa (Nairobi area: 36.82, -1.29)
    outside_poly = Polygon(
        ((36.81, -1.30), (36.83, -1.30), (36.83, -1.28), (36.81, -1.28), (36.81, -1.30)),
        srid=SRID_WGS84,
    )
    with pytest.raises(ValidationError) as exc_info:
        validate_mombasa_geographic_envelope(outside_poly)
    assert "outside Mombasa" in str(exc_info.value)


def test_validate_projection_suitability():
    valid_poly = Polygon(
        ((39.69, -4.05), (39.71, -4.05), (39.71, -4.03), (39.69, -4.03), (39.69, -4.05)),
        srid=SRID_WGS84,
    )
    validate_projection_suitability(valid_poly)  # Should not raise


def test_try_repair_geometry():
    # Valid Polygon promoted to MultiPolygon
    poly = Polygon(
        ((39.69, -4.05), (39.71, -4.05), (39.71, -4.03), (39.69, -4.03), (39.69, -4.05)),
        srid=SRID_WGS84,
    )
    healed, was_repaired, details = try_repair_geometry(poly)
    assert isinstance(healed, MultiPolygon)
    assert was_repaired is True
    assert healed.valid

    # Self-intersecting bow-tie polygon
    bowtie_wkt = (
        "POLYGON((39.69 -4.05, 39.71 -4.03, 39.71 -4.05, 39.69 -4.03, 39.69 -4.05))"
    )
    invalid_geom = GEOSGeometry(bowtie_wkt, srid=SRID_WGS84)
    assert not invalid_geom.valid
    healed, was_repaired, details = try_repair_geometry(invalid_geom)
    assert healed.valid
    assert was_repaired is True
    assert isinstance(healed, MultiPolygon)


def test_ingestion_result_invariant():
    res = IngestionResult(total_input=100, inserted=70, updated=10, unchanged=15, quarantined=5)
    assert res.is_invariant_satisfied is True

    bad_res = IngestionResult(total_input=100, inserted=70, updated=10, unchanged=15, quarantined=4)
    assert bad_res.is_invariant_satisfied is False
