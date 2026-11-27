"""STAC catalog search. The bounding box is sent only after an explicit confirmation."""

from __future__ import annotations

import json
import math
import re

from django.utils import timezone
from rest_framework import serializers

from apps.spatial.domain import MAX_CATALOG_BBOX_DEGREES, MAX_CATALOG_SEARCH_LIMIT, ProvenanceClass
from apps.spatial.services.providers.health import fetch_collections
from apps.spatial.services.providers.http import ProviderTransportError, decode_json, fetch_https
from apps.spatial.services.providers.registry import (
    get_provider,
    mark_checked,
    record_query,
    spec_or_raise,
)

_COLLECTION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,80}$")
_DATETIME = re.compile(
    r"^\d{4}-\d{2}-\d{2}(T\d{2}:\d{2}:\d{2}Z)?(/\d{4}-\d{2}-\d{2}(T\d{2}:\d{2}:\d{2}Z)?)?$"
)


def rfc3339_datetime(value: str) -> str:
    """Send a UTC day as an inclusive RFC3339 interval.

    Earth Search rejects a bare YYYY-MM-DD. A date is the whole UTC day.
    A value that already carries a time is left unchanged.
    """

    def stamp(part: str, *, end: bool) -> str:
        if "T" in part:
            return part
        return f"{part}T{'23:59:59Z' if end else '00:00:00Z'}"

    start, separator, end = value.partition("/")
    if not separator:
        if "T" in start:
            return start
        return f"{start}T00:00:00Z/{start}T23:59:59Z"
    return f"{stamp(start, end=False)}/{stamp(end, end=True)}"


_LIMITATION = (
    "Catalog metadata from the named provider. Imagery bytes were not downloaded. "
    "No land-cover change was calculated."
)


def clean_search_request(data: dict) -> dict:
    if not isinstance(data, dict):
        raise serializers.ValidationError("Send a JSON object.")
    if data.get("share_aoi_with_provider") is not True:
        raise serializers.ValidationError(
            {
                "share_aoi_with_provider": (
                    "Confirm that this bounding box may be sent to the named provider. "
                    "No other account or parcel data is attached."
                )
            }
        )
    bbox = data.get("bbox")
    if not isinstance(bbox, list) or len(bbox) != 4:
        raise serializers.ValidationError(
            {"bbox": "Send [min longitude, min latitude, max longitude, max latitude]."}
        )
    try:
        numbers = [float(item) for item in bbox]
    except (TypeError, ValueError) as exc:
        raise serializers.ValidationError({"bbox": "Bounding box values must be numbers."}) from exc
    if not all(math.isfinite(number) for number in numbers):
        raise serializers.ValidationError({"bbox": "Bounding box values must be finite."})
    min_lon, min_lat, max_lon, max_lat = numbers
    if min_lon < -180 or max_lon > 180 or min_lat < -90 or max_lat > 90:
        raise serializers.ValidationError(
            {"bbox": "Bounding box values are outside longitude and latitude bounds."}
        )
    if min_lon >= max_lon or min_lat >= max_lat:
        raise serializers.ValidationError({"bbox": "The bounding box is empty."})
    if max_lon - min_lon > MAX_CATALOG_BBOX_DEGREES or max_lat - min_lat > MAX_CATALOG_BBOX_DEGREES:
        raise serializers.ValidationError({"bbox": "Keep the box within 2 degrees on each side."})
    collection = data.get("collection")
    if not isinstance(collection, str) or _COLLECTION_ID.fullmatch(collection) is None:
        raise serializers.ValidationError(
            {"collection": "Choose a collection id from the provider catalog."}
        )
    limit = data.get("limit", 5)
    limit_ok = isinstance(limit, int) and not isinstance(limit, bool)
    if not limit_ok or not 1 <= limit <= MAX_CATALOG_SEARCH_LIMIT:
        raise serializers.ValidationError({"limit": "Limit must be an integer from 1 to 10."})
    datetime_range = data.get("datetime") or ""
    if datetime_range:
        if not isinstance(datetime_range, str) or _DATETIME.fullmatch(datetime_range) is None:
            raise serializers.ValidationError(
                {"datetime": "Use YYYY-MM-DD or YYYY-MM-DD/YYYY-MM-DD in UTC."}
            )
        if "/" in datetime_range:
            start, end = datetime_range.split("/", 1)
            if start > end:
                raise serializers.ValidationError({"datetime": "The start is after the end."})
    cloud = data.get("max_cloud_cover", None)
    if cloud is not None:
        cloud_ok = isinstance(cloud, (int, float)) and not isinstance(cloud, bool)
        if not cloud_ok or not 0 <= float(cloud) <= 100:
            raise serializers.ValidationError(
                {"max_cloud_cover": "Cloud cover must be between 0 and 100."}
            )
        cloud = float(cloud)
    return {
        "bbox": [round(number, 6) for number in numbers],
        "collection": collection,
        "limit": limit,
        "datetime": datetime_range,
        "max_cloud_cover": cloud,
    }


def _assets(feature: dict) -> list[dict]:
    raw = feature.get("assets")
    if not isinstance(raw, dict):
        return []
    cleaned = []
    for key, asset in raw.items():
        if len(cleaned) >= 8 or not isinstance(asset, dict):
            continue
        href = asset.get("href")
        if not isinstance(href, str) or not href.startswith("https://") or len(href) > 500:
            continue
        cleaned.append(
            {
                "key": str(key)[:40],
                "href": href,
                "type": str(asset.get("type") or "")[:80],
            }
        )
    return cleaned


def _features(document, limit: int) -> list[dict]:
    if not isinstance(document, dict) or document.get("type") != "FeatureCollection":
        raise ProviderTransportError("The provider response was not a STAC FeatureCollection.")
    raw = document.get("features")
    if not isinstance(raw, list):
        raise ProviderTransportError("The provider response was not a STAC FeatureCollection.")
    features = []
    for feature in raw:
        if len(features) >= limit or not isinstance(feature, dict):
            continue
        raw_properties = feature.get("properties")
        properties = raw_properties if isinstance(raw_properties, dict) else {}
        bbox = feature.get("bbox")
        if not isinstance(bbox, list) or len(bbox) != 4:
            bbox = None
        else:
            try:
                bbox = [round(float(value), 6) for value in bbox]
            except TypeError, ValueError:
                bbox = None
        features.append(
            {
                "id": str(feature.get("id") or "")[:160],
                "collection": str(feature.get("collection") or properties.get("collection") or "")[
                    :120
                ],
                "datetime": properties.get("datetime"),
                "cloud_cover": properties.get("eo:cloud_cover"),
                "bbox": bbox,
                "assets": _assets(feature),
                "evidence": ProvenanceClass.OBSERVED,
                "limitation": _LIMITATION,
            }
        )
    return features


def search_provider_catalog(*, provider_id: str, organization, user, payload: dict) -> dict:
    spec = spec_or_raise(provider_id)
    row = get_provider(provider_id)
    if not spec.stac_search:
        raise serializers.ValidationError(
            {"provider_id": "Catalog search is not enabled for this provider."}
        )
    cleaned = clean_search_request(payload)
    try:
        status, brief = fetch_collections(spec)
    except ProviderTransportError as exc:
        record_query(
            provider=row,
            organization=organization,
            user=user,
            query_kind="catalog_search",
            http_status=exc.http_status,
            operational_state="unreachable",
            collection_id=cleaned["collection"],
            error=exc.message,
        )
        raise
    mark_checked(
        row,
        http_status=status,
        state="reachable",
        summary={"collection_count": len(brief), "collections": brief[:30]},
    )
    known = {item["id"] for item in brief}
    if cleaned["collection"] not in known:
        raise serializers.ValidationError(
            {"collection": "That collection was not in the provider catalog."}
        )
    body = {
        "collections": [cleaned["collection"]],
        "bbox": cleaned["bbox"],
        "limit": cleaned["limit"],
    }
    if cleaned["datetime"]:
        body["datetime"] = rfc3339_datetime(cleaned["datetime"])
    if cleaned["max_cloud_cover"] is not None:
        body["query"] = {"eo:cloud_cover": {"lte": cleaned["max_cloud_cover"]}}
    url = spec.catalog_url.rstrip("/") + "/search"
    try:
        search_status, raw = fetch_https(
            url,
            allowed_hosts=frozenset({spec.allowed_host}),
            max_bytes=1_500_000,
            method="POST",
            body=json.dumps(body).encode("utf-8"),
            content_type="application/json",
        )
        features = _features(decode_json(raw), cleaned["limit"])
    except ProviderTransportError as exc:
        record_query(
            provider=row,
            organization=organization,
            user=user,
            query_kind="catalog_search",
            http_status=exc.http_status,
            operational_state="unreachable",
            collection_id=cleaned["collection"],
            bbox=cleaned["bbox"],
            error=exc.message,
        )
        raise
    record_query(
        provider=row,
        organization=organization,
        user=user,
        query_kind="catalog_search",
        http_status=search_status,
        operational_state="reachable",
        item_count=len(features),
        collection_id=cleaned["collection"],
        bbox=cleaned["bbox"],
    )
    return {
        "provider_id": spec.provider_id,
        "collection": cleaned["collection"],
        "queried_at": timezone.now().isoformat(),
        "limit": cleaned["limit"],
        "item_count": len(features),
        "features": features,
        "attribution": spec.attribution,
        "service_status": spec.service_status,
        "shared_with_provider": ["bbox", "collection", "datetime", "max_cloud_cover", "limit"],
        "not_shared": ["account", "organization", "parcel", "user"],
    }
