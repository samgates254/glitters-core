"""Preflight reports the host and refuses work before a job is queued."""

from collections import namedtuple

import pytest

from apps.spatial.services.compute.contracts import DatasetTooLarge
from apps.spatial.services.compute.preflight import host_snapshot, preflight_dataset, probe_gpu
from apps.spatial.services.compute.storage import read_bytes, write_bytes
from apps.spatial.services.safe_paths import UnsafeSpatialPath

Usage = namedtuple("Usage", "total used free")


def test_host_snapshot_is_a_light_workload(settings, tmp_path):
    settings.GLITTERS_COMPUTE_ROOT = str(tmp_path)
    snapshot = host_snapshot()
    assert snapshot["workload_class"] == "LIGHT"
    assert snapshot["vram_required_mb"] == 0
    assert snapshot["disk_free_bytes"] > 0
    assert snapshot["gpu"]["billable"] is False
    assert snapshot["gpu"]["status"] in {"available", "unavailable"}
    assert probe_gpu()["billable"] is False


def test_missing_nvidia_smi_does_not_launch_a_process(monkeypatch):
    monkeypatch.setattr("apps.spatial.services.compute.preflight.shutil.which", lambda _name: None)

    def fail_run(*args, **kwargs):
        raise AssertionError("nvidia-smi was executed")

    monkeypatch.setattr("apps.spatial.services.compute.preflight.subprocess.run", fail_run)
    assert probe_gpu()["status"] == "unavailable"


def test_oversized_dataset_is_rejected_before_storage(settings, tmp_path):
    settings.GLITTERS_COMPUTE_ROOT = str(tmp_path)
    settings.GLITTERS_COMPUTE_MAX_BYTES = 32
    with pytest.raises(DatasetTooLarge):
        preflight_dataset(33)
    assert list(tmp_path.rglob("*")) == []


def test_low_disk_is_rejected(settings, tmp_path, monkeypatch):
    settings.GLITTERS_COMPUTE_ROOT = str(tmp_path)
    monkeypatch.setattr(
        "apps.spatial.services.compute.preflight.shutil.disk_usage",
        lambda _path: Usage(100, 90, 8),
    )
    with pytest.raises(DatasetTooLarge):
        preflight_dataset(4)


def test_storage_stays_inside_the_organization(settings, tmp_path):
    settings.GLITTERS_COMPUTE_ROOT = str(tmp_path)
    org = "11111111-1111-1111-1111-111111111111"
    other = "22222222-2222-2222-2222-222222222222"
    write_bytes(org, f"{org}/asset.gltf", b"{}")
    assert read_bytes(org, f"{org}/asset.gltf") == b"{}"
    with pytest.raises(UnsafeSpatialPath):
        read_bytes(org, f"{other}/asset.gltf")
    with pytest.raises(UnsafeSpatialPath):
        write_bytes(org, f"{org}/../secret.gltf", b"nope")
