"""Published administrative boundaries.

Geometry is downloaded only from the provider host named on the catalogue row.
A county polygon is not a parcel, a title, or a survey.
"""

from __future__ import annotations

from urllib.parse import urlparse

from rest_framework import serializers

from apps.spatial.services.providers.http import ProviderTransportError, decode_json, fetch_https
from apps.spatial.services.providers.registry import spec_or_raise

_JSON_BYTES = 2_000_000
_TIMEOUT = 20.0
_PROPERTY_KEYS = ("shapeName", "shapeISO", "shapeID", "shapeType")
_LIMITATION = (
    "Administrative boundaries from the named provider. "
    "They are not parcel boundaries and they are not a survey."
)


def _metadata_row(document) -> dict:
    row = document[0] if isinstance(document, list) and document else document
    if not isinstance(row, dict):
        raise ProviderTransportError("The boundary metadata was not a JSON object.")
    return row


def _same_host(url: str, allowed_host: str) -> str:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    off_host = parsed.scheme != "https" or host != allowed_host.lower()
    if off_host or parsed.username or parsed.password:
        raise ProviderTransportError("The geometry link left the provider host.")
    return url


def _features(document) -> list[dict]:
    if not isinstance(document, dict) or document.get("type") != "FeatureCollection":
        raise ProviderTransportError("The boundary file was not a FeatureCollection.")
    raw = document.get("features")
    if not isinstance(raw, list) or not raw:
        raise ProviderTransportError("The boundary file listed no features.")
    features = []
    for feature in raw[:80]:
        if not isinstance(feature, dict):
            continue
        geometry = feature.get("geometry")
        kind = geometry.get("type") if isinstance(geometry, dict) else None
        if kind not in ("Polygon", "MultiPolygon"):
            continue
        raw_properties = feature.get("properties")
        properties = raw_properties if isinstance(raw_properties, dict) else {}
        kept = {}
        for key in _PROPERTY_KEYS:
            value = properties.get(key)
            if isinstance(value, str) and value.strip():
                kept[key] = value.strip()[:120]
        features.append({"type": "Feature", "properties": kept, "geometry": geometry})
    if not features:
        raise ProviderTransportError("The boundary file listed no polygon features.")
    return features


def load_published_boundary(provider_id: str) -> dict:
    """Download the provider's simplified geometry. The caller must be authorized."""

    spec = spec_or_raise(provider_id)
    if spec.check_mode != "geoboundaries":
        raise serializers.ValidationError(
            {"provider_id": "This source does not publish an administrative boundary."}
        )
    hosts = frozenset({spec.allowed_host})
    _status, body = fetch_https(
        spec.catalog_url,
        allowed_hosts=hosts,
        max_bytes=_JSON_BYTES,
        timeout=_TIMEOUT,
    )
    row = _metadata_row(decode_json(body))
    href = row.get("simplifiedGeometryGeoJSON")
    if not isinstance(href, str) or not href:
        raise ProviderTransportError("The boundary service did not publish simplified geometry.")
    geometry_url = _same_host(href, spec.allowed_host)
    _geometry_status, raw = fetch_https(
        geometry_url,
        allowed_hosts=hosts,
        max_bytes=_JSON_BYTES,
        timeout=_TIMEOUT,
    )
    features = _features(decode_json(raw))
    year = row.get("boundaryYear")
    return {
        "provider_id": spec.provider_id,
        "boundary_name": str(row.get("boundaryName") or "")[:200],
        "boundary_iso": str(row.get("boundaryISO") or "")[:16],
        "boundary_type": str(row.get("boundaryType") or "")[:40],
        "boundary_year": year if isinstance(year, int) else str(year or "")[:20],
        "license": str(row.get("boundaryLicense") or "")[:200],
        "source_authority": str(row.get("boundarySource") or "")[:200],
        "feature_count": len(features),
        "geometry_downloaded": True,
        "stored_in_database": False,
        "limitation": _LIMITATION,
        "geojson": {"type": "FeatureCollection", "features": features},
    }
