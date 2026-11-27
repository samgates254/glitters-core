"""Camera bookmarks for God's Eye. These records do not certify elevation or title."""

from __future__ import annotations

import re
from decimal import Decimal

from rest_framework import serializers

from apps.spatial.domain import (
    MAX_SCENE_BOOKMARKS,
    MAX_SCENE_LAYERS,
    SceneMode,
    ScenePlace,
)
from apps.spatial.services.crs import get_optimal_kenya_utm_srid

_LAYER_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")

_PLACES = (
    {
        "place_key": ScenePlace.NAIROBI,
        "name": "Nairobi camera reference",
        "longitude": 36.8219,
        "latitude": -1.2921,
        "height_m": 3500,
        "heading_deg": 0,
        "pitch_deg": -35,
        "note": "City-center camera. Not a parcel, title, or survey mark.",
    },
    {
        "place_key": ScenePlace.KISUMU,
        "name": "Kisumu camera reference",
        "longitude": 34.7680,
        "latitude": -0.0917,
        "height_m": 3500,
        "heading_deg": 0,
        "pitch_deg": -35,
        "note": (
            "City-center camera. Not a parcel, title, or survey mark. "
            "Proximity to Lake Victoria is not a flood determination."
        ),
    },
)


def _zone_label(epsg: int) -> str:
    if 32601 <= epsg <= 32660:
        return f"{epsg - 32600}N"
    if 32701 <= epsg <= 32760:
        return f"{epsg - 32700}S"
    return str(epsg)


def reference_places() -> list[dict]:
    """Public city cameras. UTM zones are computed, not assumed to be the same."""

    places = []
    for place in _PLACES:
        epsg = get_optimal_kenya_utm_srid(place["longitude"], place["latitude"])
        places.append(
            {
                **place,
                "utm_epsg": epsg,
                "utm_zone": _zone_label(epsg),
                "elevation_disclaimer": (
                    "This is a camera bookmark. It does not certify terrain elevation."
                ),
            }
        )
    return places


def _decimal(value, *, places: str, label: str) -> Decimal:
    try:
        number = Decimal(str(value))
    except Exception as exc:
        raise serializers.ValidationError({label: "Enter a number."}) from exc
    if not number.is_finite():
        raise serializers.ValidationError({label: "Enter a finite number."})
    return number.quantize(Decimal(places))


def _camera(payload: dict, *, require_name: bool) -> dict:
    name = str(payload.get("name", "")).strip()
    if require_name and (not name or len(name) > 80):
        raise serializers.ValidationError({"name": "Use a bookmark name of at most 80 characters."})
    longitude = _decimal(payload.get("longitude"), places="0.000001", label="longitude")
    latitude = _decimal(payload.get("latitude"), places="0.000001", label="latitude")
    height = _decimal(payload.get("height_m"), places="0.01", label="height_m")
    heading = _decimal(payload.get("heading_deg", 0), places="0.01", label="heading_deg")
    pitch = _decimal(payload.get("pitch_deg", -35), places="0.01", label="pitch_deg")
    if not Decimal("-180") <= longitude <= Decimal("180"):
        raise serializers.ValidationError({"longitude": "Longitude must be between -180 and 180."})
    if not Decimal("-90") <= latitude <= Decimal("90"):
        raise serializers.ValidationError({"latitude": "Latitude must be between -90 and 90."})
    if not Decimal("50") <= height <= Decimal("2000000"):
        raise serializers.ValidationError(
            {"height_m": "Camera height must be between 50 and 2000000 metres."}
        )
    if not Decimal("0") <= heading <= Decimal("360"):
        raise serializers.ValidationError({"heading_deg": "Heading must be between 0 and 360."})
    if not Decimal("-90") <= pitch <= Decimal("90"):
        raise serializers.ValidationError({"pitch_deg": "Pitch must be between -90 and 90."})
    cleaned = {
        "longitude": float(longitude),
        "latitude": float(latitude),
        "height_m": float(height),
        "heading_deg": float(heading),
        "pitch_deg": float(pitch),
    }
    if require_name:
        cleaned["name"] = name
    return cleaned


def clean_bookmarks(value) -> list[dict]:
    if value in (None, []):
        return []
    if not isinstance(value, list) or len(value) > MAX_SCENE_BOOKMARKS:
        raise serializers.ValidationError(
            {"bookmarks": f"Store at most {MAX_SCENE_BOOKMARKS} bookmarks."}
        )
    allowed = {"name", "longitude", "latitude", "height_m", "heading_deg", "pitch_deg"}
    cleaned = []
    for item in value:
        if not isinstance(item, dict) or set(item) - allowed:
            raise serializers.ValidationError(
                {"bookmarks": "A bookmark only stores a name and a camera."}
            )
        cleaned.append(_camera(item, require_name=True))
    return cleaned


def clean_layer_state(value) -> list[dict]:
    if value in (None, []):
        return []
    if not isinstance(value, list) or len(value) > MAX_SCENE_LAYERS:
        raise serializers.ValidationError(
            {"layer_state": f"Store at most {MAX_SCENE_LAYERS} layer switches."}
        )
    allowed = {"id", "visible", "opacity"}
    cleaned = []
    for item in value:
        if not isinstance(item, dict) or set(item) - allowed:
            raise serializers.ValidationError(
                {"layer_state": "A layer switch only stores an id, visibility, and opacity."}
            )
        layer_id = item.get("id")
        if not isinstance(layer_id, str) or _LAYER_ID.fullmatch(layer_id) is None:
            raise serializers.ValidationError(
                {"layer_state": "Layer ids use lowercase letters, digits, and hyphens."}
            )
        if not isinstance(item.get("visible"), bool):
            raise serializers.ValidationError({"layer_state": "Visibility must be true or false."})
        opacity = _decimal(item.get("opacity", 1), places="0.01", label="opacity")
        if not Decimal("0") <= opacity <= Decimal("1"):
            raise serializers.ValidationError({"opacity": "Opacity must be between 0 and 1."})
        cleaned.append({"id": layer_id, "visible": item["visible"], "opacity": float(opacity)})
    return cleaned


def clean_scene_camera(attrs: dict) -> dict:
    camera = _camera(
        {
            "longitude": attrs.get("camera_longitude"),
            "latitude": attrs.get("camera_latitude"),
            "height_m": attrs.get("camera_height_m"),
            "heading_deg": attrs.get("heading_deg", 0),
            "pitch_deg": attrs.get("pitch_deg", -35),
        },
        require_name=False,
    )
    roll = _decimal(attrs.get("roll_deg", 0), places="0.01", label="roll_deg")
    exaggeration = _decimal(
        attrs.get("terrain_exaggeration", 1), places="0.1", label="terrain_exaggeration"
    )
    if not Decimal("-180") <= roll <= Decimal("180"):
        raise serializers.ValidationError({"roll_deg": "Roll must be between -180 and 180."})
    if not Decimal("1") <= exaggeration <= Decimal("3"):
        raise serializers.ValidationError(
            {"terrain_exaggeration": "Use a display factor from 1 to 3. This is not a survey."}
        )
    if attrs.get("place_key") not in ScenePlace.ALL:
        raise serializers.ValidationError({"place_key": "Choose nairobi, kisumu, or custom."})
    if attrs.get("scene_mode", SceneMode.GLOBE_3D) not in SceneMode.ALL:
        raise serializers.ValidationError({"scene_mode": "Choose 3d, 2d, or columbus."})
    attrs["camera_longitude"] = Decimal(str(camera["longitude"]))
    attrs["camera_latitude"] = Decimal(str(camera["latitude"]))
    attrs["camera_height_m"] = Decimal(str(camera["height_m"]))
    attrs["heading_deg"] = Decimal(str(camera["heading_deg"]))
    attrs["pitch_deg"] = Decimal(str(camera["pitch_deg"]))
    attrs["roll_deg"] = roll
    attrs["terrain_exaggeration"] = exaggeration
    attrs["bookmarks"] = clean_bookmarks(attrs.get("bookmarks", []))
    attrs["layer_state"] = clean_layer_state(attrs.get("layer_state", []))
    return attrs
