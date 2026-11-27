"""COG, GLB, and bundle rejection. Invalid bytes are not treated as published."""

import json
import struct

import pytest

from apps.spatial.services.asset_validation import validate_cog, validate_glb
from apps.spatial.services.compute.contracts import PermanentComputeError


def tiny_geographic_cog() -> bytes:
    """2 by 2 uncompressed tiled TIFF in EPSG:4326."""

    count = 13
    ifd_start = 8
    ifd_end = ifd_start + 2 + count * 12 + 4
    scale_at = ifd_end
    tie_at = scale_at + 24
    keys_at = tie_at + 48
    pixels_at = keys_at + 16

    def entry(tag: int, type_id: int, value_count: int, value: bytes) -> bytes:
        return struct.pack("<HHI", tag, type_id, value_count) + value

    def long_value(value: int) -> bytes:
        return struct.pack("<I", value)

    def short_value(value: int) -> bytes:
        return struct.pack("<H", value) + b"\x00\x00"

    entries = [
        entry(256, 4, 1, long_value(2)),
        entry(257, 4, 1, long_value(2)),
        entry(258, 3, 1, short_value(8)),
        entry(259, 3, 1, short_value(1)),
        entry(262, 3, 1, short_value(1)),
        entry(277, 3, 1, short_value(1)),
        entry(322, 3, 1, short_value(2)),
        entry(323, 3, 1, short_value(2)),
        entry(324, 4, 1, long_value(pixels_at)),
        entry(325, 4, 1, long_value(4)),
        entry(33550, 12, 3, long_value(scale_at)),
        entry(33922, 12, 6, long_value(tie_at)),
        entry(34735, 3, 8, long_value(keys_at)),
    ]
    blob = bytearray(b"II*\x00" + struct.pack("<I", ifd_start) + struct.pack("<H", count))
    for item in entries:
        blob += item
    blob += struct.pack("<I", 0)
    blob += struct.pack("<3d", 0.02, 0.02, 0.0)
    blob += struct.pack("<6d", 0.0, 0.0, 0.0, 36.8, -1.28, 0.0)
    blob += struct.pack("<8H", 1, 1, 0, 1, 2048, 0, 1, 4326)
    blob += b"\x01\x02\x03\x04"
    assert len(blob) == pixels_at + 4
    return bytes(blob)


def _glb(document: dict, binary: bytes = b"") -> bytes:
    chunk = json.dumps(document, separators=(",", ":")).encode("utf-8")
    chunk += b" " * ((-len(chunk)) % 4)
    body = struct.pack("<II", len(chunk), 0x4E4F534A) + chunk
    if binary:
        body += struct.pack("<II", len(binary), 0x004E4942) + binary
    return struct.pack("<4sII", b"glTF", 2, 12 + len(body)) + body


def _mesh_glb(extra: dict | None = None) -> bytes:
    binary = struct.pack("<3f", 0.0, 0.0, 0.0)
    document = {
        "asset": {"version": "2.0"},
        "buffers": [{"byteLength": len(binary)}],
        "bufferViews": [{"buffer": 0, "byteOffset": 0, "byteLength": len(binary)}],
        "accessors": [
            {
                "bufferView": 0,
                "componentType": 5126,
                "count": 1,
                "type": "VEC3",
                "min": [0, 0, 0],
                "max": [0, 0, 0],
            }
        ],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0}}]}],
    }
    if extra:
        document.update(extra)
    return _glb(document, binary)


def test_cog_accepts_a_tiled_geographic_window():
    checked = validate_cog(tiny_geographic_cog(), expected_bounds=(36.8, -1.32, 36.84, -1.28))
    assert checked["crs"] == 4326
    assert checked["width"] == 2
    assert checked["tiles"] == 1
    west, south, east, north = checked["bounds"]
    assert west == pytest.approx(36.8)
    assert north == pytest.approx(-1.28)
    assert east == pytest.approx(36.84)
    assert south == pytest.approx(-1.32)


def test_cog_rejects_truncation_missing_crs_and_a_foreign_window():
    payload = tiny_geographic_cog()
    with pytest.raises(PermanentComputeError):
        validate_cog(payload[:24])
    with pytest.raises(PermanentComputeError):
        validate_cog(b"II*\x00rest")
    with pytest.raises(PermanentComputeError, match="expected window"):
        validate_cog(payload, expected_bounds=(0.0, 0.0, 0.1, 0.1))


def test_glb_rejects_external_and_non_finite_transforms():
    checked = validate_glb(_mesh_glb())
    assert checked["meshes"] == 1
    with pytest.raises(PermanentComputeError, match="external"):
        validate_glb(_mesh_glb({"images": [{"uri": "https://example.invalid/a.png"}]}))
    with pytest.raises(PermanentComputeError, match="embedded"):
        validate_glb(_mesh_glb({"buffers": [{"byteLength": 12, "uri": "secret.bin"}]}))
    with pytest.raises(PermanentComputeError, match="external"):
        validate_glb(_mesh_glb({"buffers": [{"byteLength": 12, "uri": "../secret.bin"}]}))
    with pytest.raises(PermanentComputeError, match="finite"):
        validate_glb(_mesh_glb({"nodes": [{"translation": [1, float("inf"), 0]}]}))
    with pytest.raises(PermanentComputeError):
        validate_glb(b"glTF")
