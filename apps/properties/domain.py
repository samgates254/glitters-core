"""Property rules that do not belong to the organization registry.

Listing publication, price, and legal-title verification are not decided here.
"""

from __future__ import annotations

import math
import secrets
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from django.contrib.gis.geos import Point

NOT_FOUND = "Not found."
PERMISSION_DENIED = "You do not have permission for this action."
ARCHIVED = "Archived properties cannot be changed."
ALREADY_ARCHIVED = "This property is already archived."
ARCHIVE_CHILDREN = "Archive subordinate properties first."
HIERARCHY_INVALID = "This property hierarchy is not valid."
CONTEXT_IMMUTABLE = "Management context cannot be changed."
CONTEXT_BOTH = "Choose a personal record or an organization, not both."
ORGANIZATION_REQUIRED = "An organization is required for an organization-managed property."
CONTEXT_MISMATCH = "A subordinate property must stay in the same management context."
ADDRESS_REQUIRED = "An active property needs a country and a street, area, or locality."
STATUS_INVALID = "This status change is not allowed."
ARCHIVE_VIA_OPERATION = "Archive this property with the archive operation."
SUBTYPE_INVALID = "This subtype is not valid for the property type."
CHARACTERISTIC_TYPE = "This characteristic is not valid for the property type."
CHARACTERISTIC_VALUE = "This characteristic value is not valid."
CHARACTERISTIC_ONCE = "Each characteristic can be set once."
COORDINATES_INVALID = "Coordinates are not valid."
LATITUDE_RANGE = "Enter a latitude between -90 and 90."
LONGITUDE_RANGE = "Enter a longitude between -180 and 180."
VERIFICATION_UNAVAILABLE = "A relationship cannot be marked verified through this API."
PARTY_EXISTS = "This relationship is already recorded."
PARTY_CLOSED = "This relationship can no longer be changed."
ACCOUNT_NOT_FOUND = "That account was not found."
ACCOUNT_CANNOT_BE_ADDED = "That account cannot be added."
ORGANIZATION_NOT_FOUND = "That organization was not found."
ORGANIZATION_PARTY_NO_ACCESS = (
    "An organization relationship does not grant its members access to the property."
)
LAST_MANAGER = "A personal property must keep a manager."
FILTER_INVALID = "This filter is not valid."
RADIUS_REQUIRED = "A nearby search needs a radius in metres."
NEAR_REQUIRED = "A radius needs a latitude and longitude."
RADIUS_RANGE = "Enter a radius between 1 and 50000 metres."
BBOX_INVALID = "This bounding box is not valid."
BBOX_TOO_LARGE = "The bounding box is too large."
CONFLICT = "This change conflicts with another update."
NO_CHANGES = "No changes were submitted."
COORDINATES_SRID = 4326
MAX_RADIUS_M = 50_000
MAX_BBOX_DEGREES = 2
REFERENCE_PREFIX = "GLT-"

SENSITIVE_METADATA_KEYS = frozenset(
    {
        "token",
        "raw_token",
        "token_hash",
        "password",
        "secret",
        "authorization",
        "cookie",
        "latitude",
        "longitude",
        "lat",
        "lon",
        "coordinates",
        "geom",
        "point",
        "srid",
        "wkt",
        "geojson",
    }
)


class ManagementContext:
    PERSONAL = "personal"
    ORGANIZATION = "organization"

    CHOICES = (
        (PERSONAL, "Personal"),
        (ORGANIZATION, "Organization"),
    )
    VALUES = frozenset(value for value, _label in CHOICES)


class PropertyStatus:
    DRAFT = "draft"
    ACTIVE = "active"
    ARCHIVED = "archived"

    CHOICES = (
        (DRAFT, "Draft"),
        (ACTIVE, "Active"),
        (ARCHIVED, "Archived"),
    )
    VALUES = frozenset(value for value, _label in CHOICES)


# Active records are not returned to draft. Archive is terminal.
STATUS_TRANSITIONS = {
    PropertyStatus.DRAFT: frozenset({PropertyStatus.ACTIVE, PropertyStatus.ARCHIVED}),
    PropertyStatus.ACTIVE: frozenset({PropertyStatus.ARCHIVED}),
    PropertyStatus.ARCHIVED: frozenset(),
}


class PropertyType:
    RESIDENTIAL = "residential"
    COMMERCIAL = "commercial"
    LAND = "land"
    INDUSTRIAL = "industrial"
    AGRICULTURAL = "agricultural"
    MIXED_USE = "mixed_use"
    HOSPITALITY = "hospitality"

    CHOICES = (
        (RESIDENTIAL, "Residential"),
        (COMMERCIAL, "Commercial"),
        (LAND, "Land"),
        (INDUSTRIAL, "Industrial"),
        (AGRICULTURAL, "Agricultural"),
        (MIXED_USE, "Mixed use"),
        (HOSPITALITY, "Hospitality"),
    )
    VALUES = frozenset(value for value, _label in CHOICES)


PROPERTY_SUBTYPES: dict[str, frozenset[str]] = {
    PropertyType.RESIDENTIAL: frozenset(
        {
            "apartment",
            "maisonette",
            "bungalow",
            "townhouse",
            "villa",
            "studio",
            "bedsitter",
            "compound_house",
            "other",
        }
    ),
    PropertyType.COMMERCIAL: frozenset(
        {"office", "retail_unit", "shopping_centre", "office_building", "other"}
    ),
    PropertyType.LAND: frozenset(
        {"residential_plot", "commercial_plot", "agricultural_plot", "mixed_use_plot", "other"}
    ),
    PropertyType.INDUSTRIAL: frozenset(
        {"warehouse", "factory", "godown", "industrial_yard", "other"}
    ),
    PropertyType.AGRICULTURAL: frozenset({"farm", "ranch", "orchard", "greenhouse", "other"}),
    PropertyType.MIXED_USE: frozenset({"mixed_building", "mixed_development", "other"}),
    PropertyType.HOSPITALITY: frozenset(
        {"hotel", "serviced_apartment", "lodge", "guest_house", "other"}
    ),
}


class HierarchyRole:
    ESTATE = "estate"
    BUILDING = "building"
    UNIT = "unit"
    STANDALONE = "standalone"

    CHOICES = (
        (ESTATE, "Estate"),
        (BUILDING, "Building"),
        (UNIT, "Unit"),
        (STANDALONE, "Standalone"),
    )
    VALUES = frozenset(value for value, _label in CHOICES)


class PartyRole:
    OWNER = "owner"
    CO_OWNER = "co_owner"
    MANAGER = "manager"
    DEVELOPER = "developer"
    REPRESENTATIVE = "representative"
    AUTHORIZED_CONTACT = "authorized_contact"

    CHOICES = (
        (OWNER, "Owner"),
        (CO_OWNER, "Co-owner"),
        (MANAGER, "Manager"),
        (DEVELOPER, "Developer"),
        (REPRESENTATIVE, "Representative"),
        (AUTHORIZED_CONTACT, "Authorized contact"),
    )
    VALUES = frozenset(value for value, _label in CHOICES)


class PartyStatus:
    CLAIMED = "claimed"
    PENDING_VERIFICATION = "pending_verification"
    VERIFIED = "verified"
    REJECTED = "rejected"
    REVOKED = "revoked"

    CHOICES = (
        (CLAIMED, "Claimed"),
        (PENDING_VERIFICATION, "Pending verification"),
        (VERIFIED, "Verified"),
        (REJECTED, "Rejected"),
        (REVOKED, "Revoked"),
    )
    VALUES = frozenset(value for value, _label in CHOICES)
    OPEN = frozenset({CLAIMED, PENDING_VERIFICATION, VERIFIED})
    # The API may record a claim or close it. It cannot verify title.
    API_WRITABLE = frozenset({CLAIMED, PENDING_VERIFICATION, REJECTED, REVOKED})


class PartySubject:
    PERSON = "person"
    ORGANIZATION = "organization"

    CHOICES = (
        (PERSON, "Person"),
        (ORGANIZATION, "Organization"),
    )
    VALUES = frozenset(value for value, _label in CHOICES)


class AccessCapability:
    VIEW = "view"
    MANAGE = "manage"

    CHOICES = (
        (VIEW, "View"),
        (MANAGE, "Manage"),
    )
    VALUES = frozenset(value for value, _label in CHOICES)


class AccessStatus:
    ACTIVE = "active"
    REVOKED = "revoked"

    CHOICES = (
        (ACTIVE, "Active"),
        (REVOKED, "Revoked"),
    )
    VALUES = frozenset(value for value, _label in CHOICES)


class AccessOrigin:
    """How a grant was created. Inherited rows point at the parent grant."""

    DIRECT = "direct"
    INHERITED = "inherited"

    CHOICES = (
        (DIRECT, "Direct"),
        (INHERITED, "Inherited"),
    )
    VALUES = frozenset(value for value, _label in CHOICES)


class AttributeKind:
    INTEGER = "integer"
    DECIMAL = "decimal"
    TEXT = "text"


LAND_USE_VALUES = frozenset(
    {
        "residential",
        "commercial",
        "agricultural",
        "industrial",
        "mixed",
        "conservation",
        "unknown",
    }
)

_RESIDENTIAL_FAMILY = frozenset(
    {PropertyType.RESIDENTIAL, PropertyType.HOSPITALITY, PropertyType.MIXED_USE}
)
_BUILT = frozenset(
    {
        PropertyType.RESIDENTIAL,
        PropertyType.COMMERCIAL,
        PropertyType.INDUSTRIAL,
        PropertyType.MIXED_USE,
        PropertyType.HOSPITALITY,
    }
)
_AREA_TYPES = frozenset(PropertyType.VALUES)


@dataclass(frozen=True)
class AttributeSpec:
    kind: str
    unit: str
    property_types: frozenset[str]
    minimum: Decimal | int
    maximum: Decimal | int
    vocabulary: frozenset[str] = frozenset()


ATTRIBUTE_CATALOGUE: dict[str, AttributeSpec] = {
    "bedrooms": AttributeSpec("integer", "count", _RESIDENTIAL_FAMILY, 0, 100),
    "bathrooms": AttributeSpec("integer", "count", _RESIDENTIAL_FAMILY, 0, 100),
    "living_areas": AttributeSpec("integer", "count", frozenset({PropertyType.RESIDENTIAL}), 0, 50),
    "floor_area_sqm": AttributeSpec("decimal", "sqm", _BUILT, Decimal("0"), Decimal("1000000000")),
    "parking_spaces": AttributeSpec("integer", "count", _AREA_TYPES, 0, 10000),
    "floor_number": AttributeSpec(
        "integer",
        "floor",
        _RESIDENTIAL_FAMILY | {PropertyType.COMMERCIAL},
        -5,
        250,
    ),
    "usable_floor_area_sqm": AttributeSpec(
        "decimal",
        "sqm",
        frozenset(
            {
                PropertyType.COMMERCIAL,
                PropertyType.INDUSTRIAL,
                PropertyType.MIXED_USE,
                PropertyType.HOSPITALITY,
            }
        ),
        Decimal("0"),
        Decimal("1000000000"),
    ),
    "floors": AttributeSpec("integer", "count", _BUILT, 0, 250),
    "plot_area_sqm": AttributeSpec(
        "decimal", "sqm", _AREA_TYPES, Decimal("0"), Decimal("1000000000")
    ),
    "land_use": AttributeSpec(
        "text",
        "text",
        frozenset({PropertyType.LAND, PropertyType.AGRICULTURAL}),
        0,
        0,
        LAND_USE_VALUES,
    ),
    "warehouse_area_sqm": AttributeSpec(
        "decimal",
        "sqm",
        frozenset({PropertyType.INDUSTRIAL}),
        Decimal("0"),
        Decimal("1000000000"),
    ),
    "building_area_sqm": AttributeSpec(
        "decimal",
        "sqm",
        frozenset({PropertyType.INDUSTRIAL, PropertyType.COMMERCIAL, PropertyType.MIXED_USE}),
        Decimal("0"),
        Decimal("1000000000"),
    ),
}

ATTRIBUTE_UNITS = frozenset(spec.unit for spec in ATTRIBUTE_CATALOGUE.values())


class EventAction:
    CREATED = "property.created"
    UPDATED = "property.updated"
    ARCHIVED = "property.archived"
    CHARACTERISTICS_UPDATED = "property.characteristics_updated"
    PARTICIPANT_ADDED = "property.participant_added"
    PARTICIPANT_UPDATED = "property.participant_updated"
    PARTICIPANT_REVOKED = "property.participant_revoked"
    ACCESS_GRANTED = "property.access_granted"
    ACCESS_REVOKED = "property.access_revoked"

    CHOICES = (
        (CREATED, "Property created"),
        (UPDATED, "Property updated"),
        (ARCHIVED, "Property archived"),
        (CHARACTERISTICS_UPDATED, "Characteristics updated"),
        (PARTICIPANT_ADDED, "Participant added"),
        (PARTICIPANT_UPDATED, "Participant updated"),
        (PARTICIPANT_REVOKED, "Participant revoked"),
        (ACCESS_GRANTED, "Access granted"),
        (ACCESS_REVOKED, "Access revoked"),
    )
    VALUES = frozenset(value for value, _label in CHOICES)


def hierarchy_parent_allowed(role: str, parent_role: str | None) -> bool:
    if role in {HierarchyRole.ESTATE, HierarchyRole.STANDALONE}:
        return parent_role is None
    if role == HierarchyRole.BUILDING:
        return parent_role in {None, HierarchyRole.ESTATE}
    if role == HierarchyRole.UNIT:
        return parent_role in {HierarchyRole.ESTATE, HierarchyRole.BUILDING}
    return False


def subtype_allowed(property_type: str, subtype: str) -> bool:
    return subtype in PROPERTY_SUBTYPES.get(property_type, frozenset())


def transition_allowed(current: str, new: str) -> bool:
    return new in STATUS_TRANSITIONS.get(current, frozenset())


def new_reference() -> str:
    return REFERENCE_PREFIX + secrets.token_hex(4).upper()


def parse_coordinates(payload: dict) -> Point:
    if not isinstance(payload, dict) or set(payload) - {"latitude", "longitude"}:
        raise ValueError(COORDINATES_INVALID)
    latitude = payload.get("latitude")
    longitude = payload.get("longitude")
    if isinstance(latitude, bool) or isinstance(longitude, bool):
        raise ValueError(COORDINATES_INVALID)
    try:
        lat = float(latitude)
        lon = float(longitude)
    except (TypeError, ValueError) as exc:
        raise ValueError(COORDINATES_INVALID) from exc
    if not math.isfinite(lat) or not math.isfinite(lon):
        raise ValueError(COORDINATES_INVALID)
    if lat < -90 or lat > 90:
        raise ValueError(LATITUDE_RANGE)
    if lon < -180 or lon > 180:
        raise ValueError(LONGITUDE_RANGE)
    return Point(lon, lat, srid=COORDINATES_SRID)


def rounded_distance_metres(metres) -> int:
    try:
        number = Decimal(str(metres))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(FILTER_INVALID) from exc
    if not number.is_finite() or number < 0:
        raise ValueError(FILTER_INVALID)
    whole = int(number.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    if whole == 0:
        return 0
    bands = (Decimal(whole) / Decimal(100)).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return max(int(bands) * 100, 100)


def normalize_attribute(property_type: str, code: str, value):
    spec = ATTRIBUTE_CATALOGUE.get(code)
    if spec is None or property_type not in spec.property_types:
        raise ValueError(CHARACTERISTIC_TYPE)
    if spec.kind == AttributeKind.INTEGER:
        if isinstance(value, bool) or isinstance(value, float) or isinstance(value, str):
            raise ValueError(CHARACTERISTIC_VALUE)
        if not isinstance(value, int):
            raise ValueError(CHARACTERISTIC_VALUE)
        if value < int(spec.minimum) or value > int(spec.maximum):
            raise ValueError(CHARACTERISTIC_VALUE)
        return {
            "value_integer": value,
            "value_decimal": None,
            "value_text": "",
            "unit": spec.unit,
        }
    if spec.kind == AttributeKind.DECIMAL:
        if isinstance(value, bool):
            raise ValueError(CHARACTERISTIC_VALUE)
        try:
            number = Decimal(str(value))
        except (InvalidOperation, ValueError) as exc:
            raise ValueError(CHARACTERISTIC_VALUE) from exc
        if not number.is_finite():
            raise ValueError(CHARACTERISTIC_VALUE)
        number = number.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        if number < Decimal(spec.minimum) or number > Decimal(spec.maximum):
            raise ValueError(CHARACTERISTIC_VALUE)
        return {
            "value_integer": None,
            "value_decimal": number,
            "value_text": "",
            "unit": spec.unit,
        }
    text = str(value or "").strip().lower()
    if text not in spec.vocabulary:
        raise ValueError(CHARACTERISTIC_VALUE)
    return {
        "value_integer": None,
        "value_decimal": None,
        "value_text": text,
        "unit": spec.unit,
    }


def attribute_api_value(kind_columns: dict):
    if kind_columns.get("value_integer") is not None:
        return kind_columns["value_integer"]
    if kind_columns.get("value_decimal") is not None:
        return kind_columns["value_decimal"]
    return kind_columns.get("value_text") or ""


def clean_metadata(metadata: dict | None):
    cleaned = {}
    for key, value in (metadata or {}).items():
        normalized = str(key).lower()
        if normalized in SENSITIVE_METADATA_KEYS:
            continue
        if isinstance(value, dict):
            cleaned[str(key)] = clean_metadata(value)
        elif isinstance(value, list):
            cleaned[str(key)] = [
                clean_metadata(item) if isinstance(item, dict) else item for item in value
            ]
        else:
            cleaned[str(key)] = value
    return cleaned
