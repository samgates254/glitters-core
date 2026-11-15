"""Unit tests for spatial domain rules, canonical URNs, and enums."""

import uuid
import pytest

from apps.spatial.domain import (
    INVALID_URN,
    EntityType,
    LegalAuthorityLevel,
    LifecycleStatus,
    VerticalDatum,
    build_canonical_urn,
    parse_canonical_urn,
    validate_canonical_urn,
)


def test_build_canonical_urn():
    test_id = uuid.uuid4()
    urn = build_canonical_urn(EntityType.BUILDING, test_id)
    assert urn == f"urn:glitters:spatial:building:{test_id}"

    parcel_id = "c1f7a28b-9e41-4e63-bf72-8d76e49120a1"
    urn_parcel = build_canonical_urn(EntityType.LAND_PARCEL, parcel_id)
    assert urn_parcel == f"urn:glitters:spatial:land_parcel:{parcel_id}"


def test_validate_canonical_urn_valid():
    test_id = uuid.uuid4()
    assert validate_canonical_urn(f"urn:glitters:spatial:building:{test_id}")
    assert validate_canonical_urn(f"urn:glitters:spatial:land_parcel:{test_id}")
    assert validate_canonical_urn(f"urn:glitters:spatial:road:{test_id}")
    assert validate_canonical_urn(f"urn:glitters:spatial:survey_observation:{test_id}")


def test_validate_canonical_urn_invalid():
    assert not validate_canonical_urn("")
    assert not validate_canonical_urn("invalid-urn")
    assert not validate_canonical_urn("urn:glitters:ke:msa:building:123")  # Old discarded format
    assert not validate_canonical_urn("urn:glitters:spatial:unknown_entity:c1f7a28b-9e41-4e63-bf72-8d76e49120a1")
    assert not validate_canonical_urn("urn:glitters:spatial:building:not-a-uuid")


def test_parse_canonical_urn():
    test_id = uuid.uuid4()
    urn = f"urn:glitters:spatial:building:{test_id}"
    entity_type, parsed_id = parse_canonical_urn(urn)
    assert entity_type == "building"
    assert parsed_id == test_id


def test_parse_canonical_urn_raises_on_invalid():
    with pytest.raises(ValueError, match=INVALID_URN):
        parse_canonical_urn("invalid:urn")


def test_domain_enum_integrity():
    assert "RESIDENTIAL" in dict(EntityType.CHOICES).keys() or "building" in EntityType.ALL
    assert LegalAuthorityLevel.STATUTORY_AUTHORITATIVE in LegalAuthorityLevel.ALL
    assert LegalAuthorityLevel.OPEN_DATA in LegalAuthorityLevel.ALL
    assert VerticalDatum.EGM2008_ORTHOMETRIC in VerticalDatum.ALL
    assert LifecycleStatus.ACTIVE in LifecycleStatus.ALL
