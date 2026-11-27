"""Live provider smoke. A skip is not a successful connection."""

import os

import pytest

from apps.spatial.services.providers.health import run_provider_check
from apps.spatial.services.providers.search import search_provider_catalog

pytestmark = pytest.mark.live

_LIVE = os.environ.get("GLITTERS_LIVE_PROVIDERS") == "1"


def _require_live():
    if not _LIVE:
        pytest.skip("Live provider smoke is off. This skip is not a successful connection.")


@pytest.mark.django_db
@pytest.mark.parametrize(
    "provider_id",
    [
        "earth-search",
        "nasa-gibs",
        "geoboundaries-ken-adm1",
        "copernicus-stac",
        "overture-stac",
    ],
)
def test_public_provider_answers(provider_id):
    _require_live()
    row = run_provider_check(provider_id)
    assert row.operational_state == "reachable", (provider_id, row.last_http_status, row.last_error)
    assert row.last_http_status == 200
    assert row.service_status != "OPEN AND USABLE"


@pytest.mark.django_db
def test_earth_search_catalog_search_uses_a_discovered_collection():
    _require_live()
    row = run_provider_check("earth-search")
    collections = row.discovered_summary.get("collections") or []
    assert collections, (row.operational_state, row.last_http_status, row.last_error)
    collection = collections[0]["id"]
    result = search_provider_catalog(
        provider_id="earth-search",
        organization=None,
        user=None,
        payload={
            "share_aoi_with_provider": True,
            "bbox": [36.80, -1.32, 36.90, -1.22],
            "collection": collection,
            "datetime": "2026-11-01/2026-11-27",
            "max_cloud_cover": 40,
            "limit": 2,
        },
    )
    assert result["collection"] == collection
    assert result["item_count"] == len(result["features"])
    assert result["item_count"] <= 2
    assert result["service_status"] == "OPEN WITH CONDITIONS"
