"""Tileset and raster reads must stay inside the configured storage roots."""

from pathlib import Path

import pytest
from django.test import override_settings

from apps.spatial.services.safe_paths import (
    UnsafeSpatialPath,
    resolve_raster_file,
    resolve_tile_file,
    resolve_tileset_document,
)


def test_spatial_files_cannot_escape_their_roots(tmp_path: Path) -> None:
    root = tmp_path / "twins"
    tiles = root / "gen" / "tiles"
    tiles.mkdir(parents=True)
    document = root / "gen" / "tileset.json"
    document.write_text('{"asset":{"version":"1.1"}}', encoding="utf-8")
    tile = tiles / "tile_0.glb"
    tile.write_bytes(b"glTF")
    outside = tmp_path / "secret.txt"
    outside.write_text("secret", encoding="utf-8")
    (tiles / "escape.glb").symlink_to(outside)

    with override_settings(SPATIAL_TILESET_ROOT=str(root), SPATIAL_RASTER_ROOT=str(root)):
        assert resolve_tileset_document(str(document)) == document.resolve()
        assert resolve_tile_file(str(document), "tile_0.glb") == tile.resolve()
        with pytest.raises(UnsafeSpatialPath):
            resolve_tileset_document(str(outside))
        with pytest.raises(UnsafeSpatialPath):
            resolve_tile_file(str(document), "..")
        with pytest.raises(UnsafeSpatialPath):
            resolve_tile_file(str(document), "../tileset.json")
        with pytest.raises(UnsafeSpatialPath):
            resolve_tile_file(str(document), "escape.glb")
        with pytest.raises(UnsafeSpatialPath):
            resolve_raster_file(str(outside))
        with pytest.raises(UnsafeSpatialPath):
            resolve_raster_file(str(root / "missing.tif"))
