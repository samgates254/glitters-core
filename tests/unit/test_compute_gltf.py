"""Structural glTF fixture checks. They do not certify the glTF specification."""

import json

import pytest

from apps.spatial.services.compute.contracts import PermanentComputeError
from apps.spatial.services.compute.gltf_job import (
    canonical_bytes,
    normalize_gltf,
    sha256_hex,
    triangle_fixture,
    validate_gltf,
)


def test_triangle_fixture_normalizes_without_changing_the_mesh_count():
    source = triangle_fixture()
    summary = validate_gltf(source)
    assert summary.mesh_count == 1
    output = normalize_gltf(source, job_id="job-1", source_sha256="abc")
    again = normalize_gltf(source, job_id="job-1", source_sha256="abc")
    assert output == again
    parsed = validate_gltf(json.loads(output))
    assert parsed.mesh_count == 1
    assert parsed.body["extras"]["glitters"]["reconstruction"] is False
    assert parsed.body["extras"]["glitters"]["photogrammetry"] is False
    assert parsed.body["extras"]["glitters"]["provenance"] == "MODELLED"
    assert sha256_hex(output) != sha256_hex(canonical_bytes(source))


@pytest.mark.parametrize(
    "document",
    [
        {"asset": {"version": "1.0"}, "meshes": [{}], "accessors": [{}], "buffers": [{}]},
        {
            "asset": {"version": "2.0"},
            "meshes": [],
            "accessors": [{}],
            "buffers": [{"byteLength": 1}],
        },
        {
            "asset": {"version": "2.0"},
            "meshes": [{}],
            "accessors": [{}],
            "buffers": [{"byteLength": 4, "uri": "data:application/octet-stream;base64,YQ=="}],
        },
    ],
)
def test_invalid_gltf_is_rejected(document):
    with pytest.raises(PermanentComputeError):
        validate_gltf(document)
