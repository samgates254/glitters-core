"""Contain geospatial file reads inside configured storage roots.

Tileset and raster endpoints must not follow a stored path outside those roots.
`Path.resolve()` removes `..` and follows symlinks before the containment check.
"""

from __future__ import annotations

from pathlib import Path

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


class UnsafeSpatialPath(Exception):
    """The stored path is missing or escapes the configured storage root."""


def _configured_root(setting_name: str) -> Path:
    configured = getattr(settings, setting_name, "") or ""
    if not configured:
        raise ImproperlyConfigured(f"{setting_name} is not configured.")
    return Path(configured).resolve()


def tileset_root() -> Path:
    return _configured_root("SPATIAL_TILESET_ROOT")


def raster_root() -> Path:
    return _configured_root("SPATIAL_RASTER_ROOT")


def _contained_file(root: Path, raw_path: str) -> Path:
    if not raw_path or not str(raw_path).strip():
        raise UnsafeSpatialPath("missing path")
    if "\x00" in str(raw_path):
        raise UnsafeSpatialPath("invalid path")
    candidate = Path(raw_path)
    if not candidate.is_absolute():
        candidate = root / candidate
    resolved = candidate.resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise UnsafeSpatialPath("path escapes storage root")
    if not resolved.is_file():
        raise UnsafeSpatialPath("file not found")
    return resolved


def resolve_tileset_document(tileset_path: str) -> Path:
    """Return tileset.json only when it lives under the tileset root."""
    resolved = _contained_file(tileset_root(), tileset_path)
    if resolved.name != "tileset.json":
        raise UnsafeSpatialPath("tileset document must be named tileset.json")
    return resolved


def resolve_tile_file(tileset_path: str, tile_name: str) -> Path:
    """Return one tile file stored beside a contained tileset document."""
    document = resolve_tileset_document(tileset_path)
    if not tile_name or tile_name != Path(tile_name).name:
        raise UnsafeSpatialPath("invalid tile name")
    if tile_name in {".", ".."} or "/" in tile_name or "\\" in tile_name or "\x00" in tile_name:
        raise UnsafeSpatialPath("invalid tile name")
    tile = (document.parent / "tiles" / tile_name).resolve()
    generation_dir = document.parent.resolve()
    if not tile.is_relative_to(generation_dir):
        raise UnsafeSpatialPath("tile escapes generation directory")
    if not tile.is_file():
        raise UnsafeSpatialPath("tile not found")
    return tile


def resolve_raster_file(storage_path: str) -> Path:
    """Return a raster file only when it lives under the raster root."""
    return _contained_file(raster_root(), storage_path)
