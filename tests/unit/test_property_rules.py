"""Pure property and location rules."""

import pytest
from django.contrib.gis.geos import Point

from apps.properties.domain import (
    CHARACTERISTIC_TYPE,
    CHARACTERISTIC_VALUE,
    LATITUDE_RANGE,
    AccessCapability,
    HierarchyRole,
    PropertyType,
    clean_metadata,
    hierarchy_parent_allowed,
    normalize_attribute,
    parse_coordinates,
    rounded_distance_metres,
    subtype_allowed,
)
from apps.properties.errors import PropertyError
from apps.properties.filters import parse_property_filters


def test_hierarchy_rules() -> None:
    assert hierarchy_parent_allowed(HierarchyRole.ESTATE, None)
    assert hierarchy_parent_allowed(HierarchyRole.STANDALONE, None)
    assert hierarchy_parent_allowed(HierarchyRole.BUILDING, None)
    assert hierarchy_parent_allowed(HierarchyRole.BUILDING, HierarchyRole.ESTATE)
    assert hierarchy_parent_allowed(HierarchyRole.UNIT, HierarchyRole.BUILDING)
    assert hierarchy_parent_allowed(HierarchyRole.UNIT, HierarchyRole.ESTATE)
    assert not hierarchy_parent_allowed(HierarchyRole.UNIT, None)
    assert not hierarchy_parent_allowed(HierarchyRole.UNIT, HierarchyRole.UNIT)
    assert not hierarchy_parent_allowed(HierarchyRole.ESTATE, HierarchyRole.BUILDING)
    assert not hierarchy_parent_allowed(HierarchyRole.STANDALONE, HierarchyRole.ESTATE)
    assert not hierarchy_parent_allowed(HierarchyRole.BUILDING, HierarchyRole.UNIT)


def test_subtype_pairs() -> None:
    assert subtype_allowed(PropertyType.RESIDENTIAL, "apartment")
    assert subtype_allowed(PropertyType.INDUSTRIAL, "warehouse")
    assert not subtype_allowed(PropertyType.RESIDENTIAL, "warehouse")
    assert not subtype_allowed(PropertyType.LAND, "apartment")


def test_attribute_bounds_and_vocabulary() -> None:
    bedrooms = normalize_attribute(PropertyType.RESIDENTIAL, "bedrooms", 3)
    assert bedrooms["value_integer"] == 3
    assert bedrooms["unit"] == "count"
    area = normalize_attribute(PropertyType.LAND, "plot_area_sqm", "500.5")
    assert str(area["value_decimal"]) == "500.50"
    land_use = normalize_attribute(PropertyType.LAND, "land_use", "Residential")
    assert land_use["value_text"] == "residential"
    with pytest.raises(ValueError, match=CHARACTERISTIC_TYPE):
        normalize_attribute(PropertyType.LAND, "bedrooms", 2)
    with pytest.raises(ValueError, match=CHARACTERISTIC_VALUE):
        normalize_attribute(PropertyType.RESIDENTIAL, "bedrooms", 2.5)
    with pytest.raises(ValueError, match=CHARACTERISTIC_VALUE):
        normalize_attribute(PropertyType.LAND, "land_use", "palace")


def test_coordinates_use_wgs84_axis_order() -> None:
    point = parse_coordinates({"latitude": -1.2921, "longitude": 36.8219})
    assert isinstance(point, Point)
    assert point.srid == 4326
    assert point.x == pytest.approx(36.8219)
    assert point.y == pytest.approx(-1.2921)
    with pytest.raises(ValueError, match=LATITUDE_RANGE):
        parse_coordinates({"latitude": 120, "longitude": 36})
    with pytest.raises(ValueError, match="Coordinates are not valid."):
        parse_coordinates({"latitude": -1.2, "longitude": 36.8, "srid": 3857})
    with pytest.raises(ValueError, match="Coordinates are not valid."):
        parse_coordinates({"latitude": float("nan"), "longitude": 36})


def test_distance_is_rounded_to_100_metre_bands() -> None:
    assert rounded_distance_metres(0) == 0
    assert rounded_distance_metres(11) == 100
    assert rounded_distance_metres(150) == 200
    assert rounded_distance_metres(DecimalMetres()) == 100


class DecimalMetres:
    def __str__(self) -> str:
        return "40"


def test_metadata_drops_coordinates_and_secrets() -> None:
    cleaned = clean_metadata(
        {
            "status": "active",
            "latitude": -1.29,
            "nested": {"longitude": 36.8, "name": "Kilimani"},
            "coordinates_updated": True,
            "password": "no",
        }
    )
    assert cleaned == {
        "status": "active",
        "nested": {"name": "Kilimani"},
        "coordinates_updated": True,
    }
    assert "-1.29" not in str(cleaned)


def test_filter_parser_rejects_a_huge_box() -> None:
    parsed = parse_property_filters({"near": "-1.29,36.82", "radius_m": "1000"})
    assert parsed.radius_m == 1000
    assert parsed.near.srid == 4326
    with pytest.raises(PropertyError, match="bounding box is too large"):
        parse_property_filters({"bbox": "30,-5,36,2"})
    with pytest.raises(PropertyError, match="radius"):
        parse_property_filters({"near": "-1.29,36.82"})


def test_capability_names_are_not_title_states() -> None:
    assert AccessCapability.MANAGE != "verified"
    assert "verified" not in AccessCapability.VALUES
