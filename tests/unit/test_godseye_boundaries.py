"""Boundary downloads must not follow a link off the provider host."""

import pytest

from apps.spatial.services.providers.boundaries import load_published_boundary
from apps.spatial.services.providers.http import ProviderTransportError


def test_boundary_geometry_link_must_stay_on_the_catalogue_host(monkeypatch):
    def fake_fetch(url, **kwargs):
        return 200, (
            b'[{"boundaryName":"Kenya","simplifiedGeometryGeoJSON":'
            b'"https://evil.example/kenya.geojson"}]'
        )

    monkeypatch.setattr("apps.spatial.services.providers.boundaries.fetch_https", fake_fetch)
    with pytest.raises(ProviderTransportError, match="left the provider host"):
        load_published_boundary("geoboundaries-ken-adm1")
