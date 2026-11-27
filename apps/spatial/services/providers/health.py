"""Provider health checks. Credentials are never invented.

Redirects stay on the allow-listed HTTPS host. A ping does not change the
licence class stored on the provider.
"""

from __future__ import annotations

import re

from apps.spatial.domain import ProviderOperationalState
from apps.spatial.services.providers.catalog import ProviderSpec
from apps.spatial.services.providers.http import ProviderTransportError, decode_json, fetch_https
from apps.spatial.services.providers.registry import (
    get_provider,
    mark_checked,
    mark_unchecked,
    record_query,
    spec_or_raise,
)

# Copernicus Data Space returns a few hundred collections in one document of
# about 3 MB. The read stays capped.
_STAC_BYTES = 6_000_000
_STAC_TIMEOUT = 20.0
_WMTS_BYTES = 8_000_000
_JSON_BYTES = 1_000_000


def _hosts(spec: ProviderSpec) -> frozenset[str]:
    return frozenset({spec.allowed_host})


def fetch_collections(spec: ProviderSpec) -> tuple[int, list[dict]]:
    """Read collection ids from the provider. The caller must already allow this host."""

    url = spec.catalog_url.rstrip("/") + "/collections"
    status, body = fetch_https(
        url,
        allowed_hosts=_hosts(spec),
        max_bytes=_STAC_BYTES,
        timeout=_STAC_TIMEOUT,
    )
    document = decode_json(body)
    collections = document.get("collections") if isinstance(document, dict) else None
    if not isinstance(collections, list):
        raise ProviderTransportError("The provider catalog did not list collections.")
    brief = []
    for item in collections:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        brief.append(
            {
                "id": str(item["id"])[:120],
                "title": str(item.get("title") or "")[:160],
                "license": str(item.get("license") or "")[:120],
            }
        )
    if not brief:
        raise ProviderTransportError("The provider catalog did not list collections.")
    return status, brief


def _check_stac(spec: ProviderSpec) -> tuple[int, dict]:
    status, body = fetch_https(
        spec.catalog_url,
        allowed_hosts=_hosts(spec),
        max_bytes=_STAC_BYTES,
        timeout=_STAC_TIMEOUT,
    )
    document = decode_json(body)
    if not isinstance(document, dict):
        raise ProviderTransportError("The provider response was not a STAC document.")
    summary = {"stac_type": str(document.get("type") or "")[:40]}
    if spec.stac_search:
        _collection_status, brief = fetch_collections(spec)
        summary["collection_count"] = len(brief)
        summary["collections"] = brief[:30]
    return status, summary


_LAYER_BLOCK = re.compile(
    r"<(?:[\w.-]+:)?Layer\b[^>]*>(.*?)</(?:[\w.-]+:)?Layer>",
    flags=re.IGNORECASE | re.DOTALL,
)
_IDENTIFIER = re.compile(
    r"<(?:[\w.-]+:)?Identifier>([^<]{1,120})</(?:[\w.-]+:)?Identifier>",
    flags=re.IGNORECASE,
)


def _check_wmts(spec: ProviderSpec) -> tuple[int, dict]:
    status, body = fetch_https(spec.catalog_url, allowed_hosts=_hosts(spec), max_bytes=_WMTS_BYTES)
    lowered = body.lower()
    if b"<!doctype" in lowered or b"<!entity" in lowered:
        raise ProviderTransportError("The capabilities document was rejected.")
    text = body.decode("utf-8", errors="replace")
    names: list[str] = []
    layer_count = 0
    for block in _LAYER_BLOCK.findall(text):
        layer_count += 1
        if len(names) >= 20:
            continue
        found = _IDENTIFIER.search(block)
        if found:
            names.append(found.group(1).strip())
    if layer_count == 0:
        raise ProviderTransportError("The capabilities document listed no layers.")
    return status, {"layer_count": layer_count, "layer_identifiers": names}


def _check_geoboundaries(spec: ProviderSpec) -> tuple[int, dict]:
    status, body = fetch_https(spec.catalog_url, allowed_hosts=_hosts(spec), max_bytes=_JSON_BYTES)
    document = decode_json(body)
    row = document[0] if isinstance(document, list) and document else document
    if not isinstance(row, dict):
        raise ProviderTransportError("The boundary metadata was not a JSON object.")
    kept = {}
    for key in (
        "boundaryName",
        "boundaryISO",
        "boundaryType",
        "boundaryYear",
        "boundaryYearRepresented",
        "boundaryLicense",
        "boundarySource",
    ):
        value = row.get(key)
        if isinstance(value, str):
            kept[key] = value[:200]
        elif isinstance(value, int) and key == "boundaryYear":
            kept[key] = value
    if "boundaryName" not in kept and "boundaryISO" not in kept:
        raise ProviderTransportError("The boundary metadata did not name the dataset.")
    kept["geometry_downloaded"] = False
    return status, kept


def _check_json_catalog(spec: ProviderSpec) -> tuple[int, dict]:
    status, body = fetch_https(spec.catalog_url, allowed_hosts=_hosts(spec), max_bytes=_JSON_BYTES)
    document = decode_json(body)
    if not isinstance(document, dict):
        raise ProviderTransportError("The catalog document was not JSON.")
    links = document.get("links")
    return status, {
        "stac_type": str(document.get("type") or "")[:40],
        "catalog_id": str(document.get("id") or "")[:120],
        "link_count": len(links) if isinstance(links, list) else 0,
        "children_followed": False,
    }


def run_provider_check(provider_id: str, *, user=None, organization=None):
    """Contact a provider only when the catalogue says a check is allowed."""

    spec = spec_or_raise(provider_id)
    row = get_provider(provider_id)
    if spec.credential_required or spec.check_mode == "none":
        state = (
            ProviderOperationalState.CREDENTIALS_REQUIRED
            if spec.credential_required
            else ProviderOperationalState.NOT_CONTACTED
        )
        error = (
            "No credential is configured. The provider was not contacted."
            if spec.credential_required
            else "This source is registered and was not contacted."
        )
        row = mark_unchecked(row, state=state, error=error)
        record_query(
            provider=row,
            organization=organization,
            user=user,
            query_kind="health",
            http_status=None,
            operational_state=state,
            error=error,
        )
        return row

    checkers = {
        "stac_root": _check_stac,
        "wmts_capabilities": _check_wmts,
        "geoboundaries": _check_geoboundaries,
        "json_catalog": _check_json_catalog,
    }
    checker = checkers.get(spec.check_mode)
    if checker is None:
        row = mark_unchecked(
            row,
            state=ProviderOperationalState.NOT_CONTACTED,
            error="No check is implemented for this source.",
        )
        return row
    try:
        status, summary = checker(spec)
    except ProviderTransportError as exc:
        row = mark_checked(
            row,
            http_status=exc.http_status,
            state=ProviderOperationalState.UNREACHABLE,
            error=exc.message,
        )
        record_query(
            provider=row,
            organization=organization,
            user=user,
            query_kind="health",
            http_status=exc.http_status,
            operational_state=row.operational_state,
            error=exc.message,
        )
        return row
    row = mark_checked(
        row,
        http_status=status,
        state=ProviderOperationalState.REACHABLE,
        summary=summary,
    )
    record_query(
        provider=row,
        organization=organization,
        user=user,
        query_kind="health",
        http_status=status,
        operational_state=row.operational_state,
        item_count=int(summary.get("collection_count") or summary.get("layer_count") or 0),
    )
    return row
