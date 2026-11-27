"""Derived terrain and building products. Rental APIs are not called."""

from __future__ import annotations

from django.conf import settings

from apps.spatial.services.city_models import city_document, validate_city_document
from apps.spatial.services.compute.contracts import (
    OPERATION_BUILDINGS,
    OPERATION_TERRAIN,
    PROVENANCE_DOWNLOADED,
    PROVENANCE_MODELLED,
    DatasetTooLarge,
    PermanentComputeError,
)
from apps.spatial.services.compute.gltf_job import canonical_bytes
from apps.spatial.services.providers.http import ProviderTransportError
from apps.spatial.services.surroundings import load_surroundings
from apps.spatial.services.terrain_surface import (
    fetch_place_asc,
    height_grid_from_asc,
    place_extent,
    validate_height_grid,
)

RESERVE_BYTES = 500_000


def _limit(payload: bytes, label: str) -> None:
    limit = int(getattr(settings, "GLITTERS_COMPUTE_MAX_BYTES", 2_000_000))
    if len(payload) > limit:
        raise DatasetTooLarge(f"{label} exceeds the CPU development limit. It was not published.")


def produce_surface(operation: str, place: str) -> dict:
    if operation == OPERATION_TERRAIN:
        return _terrain(place)
    if operation == OPERATION_BUILDINGS:
        return _buildings(place)
    raise PermanentComputeError("That surface operation is not available.")


def _terrain(place: str) -> dict:
    try:
        asc = fetch_place_asc(place)
    except ProviderTransportError as exc:
        raise PermanentComputeError(str(exc)[:240] or "The elevation window was not read.") from exc
    source = asc.encode("utf-8")
    _limit(source, "Elevation window")
    document = height_grid_from_asc(asc, place=place)
    validate_height_grid(document)
    output = canonical_bytes(document)
    _limit(output, "Height grid")
    if sha_pair_same(source, output):
        raise PermanentComputeError("The height grid was not a transformation of the DEM.")
    note = str(document["limitation"])[:400]
    return _bundle(
        source=source,
        output=output,
        source_type="dem",
        source_media="text/plain",
        source_suffix=".asc",
        output_type="terrain",
        output_media="application/json",
        output_suffix=".json",
        note=note,
        license_identifier=str(document["license"])[:80],
        attribution=str(document["attribution"])[:240],
        provenance={
            "representation": "height-grid",
            "source_kind": "DSM",
            "geoid_applied": False,
            "bare_earth": False,
            "survey": False,
            "photogrammetry": False,
            "reconstruction": False,
            "browser_heightmap": True,
            "browser_tiles": False,
            "place": place,
            "vertical_datum": "EGM2008",
            "tile_id": document["tile_id"],
            "elevation_mean_m": document["elevation_mean_m"],
            "nodata_filled": document["nodata_filled"],
        },
    )


def _buildings(place: str) -> dict:
    try:
        report = load_surroundings(place_extent(place))
    except ProviderTransportError as exc:
        message = str(exc)[:240] or "OpenStreetMap was not read."
        raise PermanentComputeError(message) from exc
    document = city_document(place, report)
    validate_city_document(document)
    output = canonical_bytes(document)
    _limit(output, "Building collection")
    source = canonical_bytes(report)
    _limit(source, "Map extract")
    if source == output:
        raise PermanentComputeError("The building collection was not a transformation.")
    note = str(document["limitation"])[:400]
    return _bundle(
        source=source,
        output=output,
        source_type="vector",
        source_media="application/json",
        source_suffix=".json",
        output_type="vector",
        output_media="application/geo+json",
        output_suffix=".geojson",
        note=note,
        license_identifier=str(document["license"])[:80],
        attribution=str(document["attribution"])[:240],
        provenance={
            "representation": "extruded-footprint",
            "photogrammetry": False,
            "reconstruction": False,
            "survey": False,
            "browser_tiles": False,
            "place": place,
            "building_count": document["building_count"],
            "height_basis_counts": document["height_basis_counts"],
        },
    )


def sha_pair_same(source: bytes, output: bytes) -> bool:
    return source == output


def _bundle(
    *,
    source: bytes,
    output: bytes,
    source_type: str,
    source_media: str,
    source_suffix: str,
    output_type: str,
    output_media: str,
    output_suffix: str,
    note: str,
    license_identifier: str,
    attribution: str,
    provenance: dict,
) -> dict:
    return {
        "source_bytes": source,
        "source_type": source_type,
        "source_media": source_media,
        "source_suffix": source_suffix,
        "source_provenance": PROVENANCE_DOWNLOADED,
        "output_bytes": output,
        "output_type": output_type,
        "output_media": output_media,
        "output_suffix": output_suffix,
        "output_provenance": PROVENANCE_MODELLED,
        "output_status": "published",
        "note": note,
        "license_identifier": license_identifier,
        "attribution": attribution,
        "provenance": provenance,
    }
