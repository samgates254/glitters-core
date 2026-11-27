"""Host checks before a job is queued. Missing hardware does not rent capacity."""

from __future__ import annotations

import os
import shutil
import subprocess

from django.conf import settings

from apps.spatial.services.compute.contracts import DatasetTooLarge
from apps.spatial.services.compute.storage import compute_root


def _ram_bytes() -> int | None:
    try:
        pages = os.sysconf("SC_PHYS_PAGES")
        page_size = os.sysconf("SC_PAGE_SIZE")
    except (AttributeError, OSError, ValueError):
        pages = 0
        page_size = 0
    if isinstance(pages, int) and isinstance(page_size, int) and pages > 0 and page_size > 0:
        return pages * page_size
    meminfo = "/proc/meminfo"
    try:
        for line in open(meminfo, encoding="utf-8"):
            if line.startswith("MemTotal:"):
                kib = int(line.split()[1])
                return kib * 1024
    except (OSError, IndexError, ValueError):
        return None
    return None


def probe_gpu() -> dict:
    """Read local VRAM. Absence is unavailable, not a cue to call a rental API."""

    program = shutil.which("nvidia-smi")
    if not program:
        return {
            "status": "unavailable",
            "vram_mb": None,
            "billable": False,
            "reason": "nvidia-smi is not installed.",
        }
    command = [program, "--query-gpu=memory.total", "--format=csv,noheader,nounits"]
    try:
        completed = subprocess.run(  # noqa: S603
            command,
            check=False,
            capture_output=True,
            timeout=3,
            text=True,
        )
    except (OSError, subprocess.TimeoutExpired):
        return {
            "status": "unavailable",
            "vram_mb": None,
            "billable": False,
            "reason": "nvidia-smi did not answer.",
        }
    first = (completed.stdout or "").strip().splitlines()
    if completed.returncode != 0 or not first:
        return {
            "status": "unavailable",
            "vram_mb": None,
            "billable": False,
            "reason": "nvidia-smi returned no VRAM reading.",
        }
    try:
        vram = int(first[0].strip())
    except ValueError:
        return {
            "status": "unavailable",
            "vram_mb": None,
            "billable": False,
            "reason": "nvidia-smi returned no VRAM reading.",
        }
    return {"status": "available", "vram_mb": vram, "billable": False, "reason": ""}


def host_snapshot() -> dict:
    root = compute_root()
    disk = shutil.disk_usage(root)
    return {
        "disk_free_bytes": disk.free,
        "disk_total_bytes": disk.total,
        "ram_available_bytes": _ram_bytes(),
        "gpu": probe_gpu(),
        "workload_class": "LIGHT",
        "vram_required_mb": 0,
    }


def preflight_dataset(byte_size: int) -> dict:
    limit = int(getattr(settings, "GLITTERS_COMPUTE_MAX_BYTES", 2_000_000))
    if byte_size > limit:
        raise DatasetTooLarge(
            f"Dataset exceeds the CPU development limit of {limit} bytes. It was not queued."
        )
    snapshot = host_snapshot()
    snapshot["dataset_bytes"] = byte_size
    snapshot["max_bytes"] = limit
    if snapshot["disk_free_bytes"] < byte_size * 4:
        raise DatasetTooLarge("Not enough free disk to keep the source and a derived copy.")
    return snapshot
