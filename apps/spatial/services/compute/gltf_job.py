"""Structural glTF checks for the CPU fixture pipeline.

These checks are not a glTF specification certification and not a reconstruction.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import struct
from dataclasses import dataclass

from apps.spatial.services.compute.contracts import FIXTURE_TRIANGLE, PermanentComputeError

_COMPONENT_FLOAT = 5126
_MODE_TRIANGLES = 4


@dataclass(frozen=True)
class GltfSummary:
    mesh_count: int
    body: dict


def sha256_hex(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def canonical_bytes(document: dict) -> bytes:
    return json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8")


def triangle_fixture() -> dict:
    """A one-triangle glTF 2.0 document written by this repository."""

    positions = struct.pack("<9f", 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0)
    encoded = base64.b64encode(positions).decode("ascii")
    return {
        "asset": {"version": "2.0", "generator": "glitters-compute-fixture"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{"mesh": 0, "name": "fixture-triangle"}],
        "meshes": [
            {
                "name": "fixture-triangle",
                "primitives": [{"attributes": {"POSITION": 0}, "mode": _MODE_TRIANGLES}],
            }
        ],
        "accessors": [
            {
                "bufferView": 0,
                "componentType": _COMPONENT_FLOAT,
                "count": 3,
                "type": "VEC3",
                "min": [0.0, 0.0, 0.0],
                "max": [1.0, 1.0, 0.0],
            }
        ],
        "bufferViews": [{"buffer": 0, "byteOffset": 0, "byteLength": 36, "target": 34962}],
        "buffers": [
            {
                "byteLength": 36,
                "uri": f"data:application/octet-stream;base64,{encoded}",
            }
        ],
        "extras": {"fixture": FIXTURE_TRIANGLE},
    }


def _data_uri_length(uri: str) -> int:
    marker = "base64,"
    if marker not in uri:
        raise PermanentComputeError("glTF data URI is not base64.")
    encoded = uri.split(marker, 1)[1]
    try:
        return len(base64.b64decode(encoded, validate=True))
    except ValueError as exc:
        raise PermanentComputeError("glTF data URI could not be decoded.") from exc


def validate_gltf(document: object) -> GltfSummary:
    if not isinstance(document, dict):
        raise PermanentComputeError("glTF document must be a JSON object.")
    asset = document.get("asset")
    version = asset.get("version") if isinstance(asset, dict) else ""
    if not isinstance(version, str) or not version.startswith("2."):
        raise PermanentComputeError("glTF asset.version must be 2.x.")
    meshes = document.get("meshes")
    if not isinstance(meshes, list) or not meshes:
        raise PermanentComputeError("glTF meshes must be a non-empty list.")
    accessors = document.get("accessors")
    if not isinstance(accessors, list) or not accessors:
        raise PermanentComputeError("glTF accessors must be a non-empty list.")
    buffers = document.get("buffers")
    if not isinstance(buffers, list) or not buffers:
        raise PermanentComputeError("glTF buffers must be a non-empty list.")
    for buffer in buffers:
        if not isinstance(buffer, dict) or not isinstance(buffer.get("byteLength"), int):
            raise PermanentComputeError("Each glTF buffer needs a positive byteLength.")
        if buffer["byteLength"] <= 0:
            raise PermanentComputeError("Each glTF buffer needs a positive byteLength.")
        uri = buffer.get("uri")
        if isinstance(uri, str) and uri.startswith("data:"):
            if _data_uri_length(uri) != buffer["byteLength"]:
                raise PermanentComputeError("glTF buffer byteLength does not match its data URI.")
    for accessor in accessors:
        if not isinstance(accessor, dict) or "bufferView" not in accessor:
            raise PermanentComputeError("Each glTF accessor needs a bufferView.")
    return GltfSummary(mesh_count=len(meshes), body=document)


def normalize_gltf(document: dict, *, job_id: str, source_sha256: str) -> bytes:
    parsed = validate_gltf(document)
    body = copy.deepcopy(parsed.body)
    extras = body.get("extras")
    if not isinstance(extras, dict):
        extras = {}
    extras["glitters"] = {
        "provenance": "MODELLED",
        "operation": "gltf_normalize",
        "source_sha256": source_sha256,
        "job_id": job_id,
        "reconstruction": False,
        "photogrammetry": False,
        "note": "Structural fixture normalization. Not a photogrammetric mesh and not a survey.",
    }
    body["extras"] = extras
    raw = canonical_bytes(body)
    again = validate_gltf(json.loads(raw))
    if again.mesh_count != parsed.mesh_count or again.mesh_count < 1:
        raise PermanentComputeError("Normalized mesh count changed.")
    if sha256_hex(raw) != sha256_hex(canonical_bytes(again.body)):
        raise PermanentComputeError("Normalized document hash did not round-trip.")
    return raw
