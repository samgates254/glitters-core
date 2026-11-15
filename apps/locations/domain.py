"""Location reference rules.

Countries and administrative ladders are data. Kenya is the first market,
not a column in the schema.
"""

from __future__ import annotations

import re

COUNTRY_RE = re.compile(r"^[A-Z]{2}$")
LEVEL_CODE_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
MAX_CHAIN_DEPTH = 12

AUTHENTICATION_REQUIRED = "Authentication is required."
ACCOUNT_BLOCKED = "This account cannot sign in."
LOCATION_NOT_FOUND = "Location not found."
COUNTRY_UNSUPPORTED = "This country is not supported."
AREA_COUNTRY = "This administrative area does not belong to the selected country."
LOCALITY_COUNTRY = "This locality does not belong to the selected country."
LOCALITY_OUTSIDE = "This locality is outside the selected administrative area."
AREA_INVALID = "This administrative area is not valid."
ADDRESS_INCOMPLETE = "Enter an area, locality, or street."
SEARCH_TOO_SHORT = "Enter at least two characters."
ADDRESS_FIELD_INVALID = "This address is not valid."


class LocalityKind:
    CITY = "city"
    TOWN = "town"
    NEIGHBOURHOOD = "neighbourhood"
    OTHER = "other"

    CHOICES = (
        (CITY, "City"),
        (TOWN, "Town"),
        (NEIGHBOURHOOD, "Neighbourhood"),
        (OTHER, "Other"),
    )
    VALUES = frozenset(value for value, _label in CHOICES)


def normalize_country(value) -> str:
    code = str(value or "").strip().upper()
    if not COUNTRY_RE.fullmatch(code):
        raise ValueError(COUNTRY_UNSUPPORTED)
    return code


def clean_text(value, *, max_length: int) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError(ADDRESS_FIELD_INVALID)
    text = value.strip()
    if len(text) > max_length:
        raise ValueError(ADDRESS_FIELD_INVALID)
    if any(ord(character) < 32 for character in text):
        raise ValueError(ADDRESS_FIELD_INVALID)
    return text
