"""Provider catalogue rules that must stay true without a network call."""

from urllib.parse import urlparse

import pytest
from rest_framework.exceptions import ValidationError

from apps.spatial.domain import ProviderServiceStatus
from apps.spatial.services.providers.catalog import PROVIDERS
from apps.spatial.services.providers.search import clean_search_request, rfc3339_datetime


def test_every_provider_has_an_allow_listed_https_host():
    ids = [spec.provider_id for spec in PROVIDERS]
    assert len(ids) == len(set(ids))
    for spec in PROVIDERS:
        parsed = urlparse(spec.catalog_url)
        assert parsed.scheme == "https"
        assert parsed.hostname == spec.allowed_host
        assert spec.service_status in ProviderServiceStatus.ALL


def test_restricted_sources_are_not_described_as_open():
    by_id = {spec.provider_id: spec for spec in PROVIDERS}
    assert by_id["open-meteo"].service_status == ProviderServiceStatus.FREE_TIER_RESTRICTED
    assert by_id["nominatim-public"].service_status == ProviderServiceStatus.FREE_TIER_RESTRICTED
    assert by_id["nasa-firms"].credential_required is True
    assert by_id["nasa-firms"].check_mode == "none"
    assert by_id["ardhisasa"].service_status == ProviderServiceStatus.UNAVAILABLE
    assert by_id["ardhisasa"].check_mode == "none"
    assert by_id["opentopography"].service_status == ProviderServiceStatus.COMMERCIAL
    assert by_id["earth-search"].stac_search is True
    assert by_id["copernicus-dem"].check_mode == "none"
    assert "30 m" in by_id["copernicus-dem"].resolution_note
    assert "not proof" in by_id["nasa-firms"].resolution_note


def test_catalog_search_refuses_to_send_an_aoi_without_confirmation():
    with pytest.raises(ValidationError):
        clean_search_request(
            {
                "share_aoi_with_provider": False,
                "bbox": [36.80, -1.30, 36.85, -1.25],
                "collection": "sentinel-2-l2a",
            }
        )


def test_a_utc_day_is_sent_as_an_rfc3339_interval():
    assert rfc3339_datetime("2026-11-01/2026-11-27") == (
        "2026-11-01T00:00:00Z/2026-11-27T23:59:59Z"
    )
    assert rfc3339_datetime("2026-11-01") == "2026-11-01T00:00:00Z/2026-11-01T23:59:59Z"
    kept = "2026-11-01T00:00:00Z/2026-11-27T12:00:00Z"
    assert rfc3339_datetime(kept) == kept


def test_catalog_search_rejects_a_wide_box_and_a_boolean_limit():
    with pytest.raises(ValidationError):
        clean_search_request(
            {
                "share_aoi_with_provider": True,
                "bbox": [36.0, -2.0, 39.5, -1.0],
                "collection": "sentinel-2-l2a",
            }
        )
    with pytest.raises(ValidationError):
        clean_search_request(
            {
                "share_aoi_with_provider": True,
                "bbox": [36.80, -1.30, 36.85, -1.25],
                "collection": "sentinel-2-l2a",
                "limit": True,
            }
        )
