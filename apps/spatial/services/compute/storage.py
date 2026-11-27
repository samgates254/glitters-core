"""Organization-scoped compute files. API responses never receive these paths."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from django.conf import settings

from apps.spatial.services.safe_paths import UnsafeSpatialPath


def compute_root() -> Path:
    configured = getattr(settings, "GLITTERS_COMPUTE_ROOT", "") or ""
    if configured:
        root = Path(configured)
    else:
        root = Path(tempfile.gettempdir()) / "glitters-compute"
    root.mkdir(parents=True, mode=0o700, exist_ok=True)
    return root.resolve()


def assert_org_key(organization_id: str, storage_key: str) -> str:
    """Reject absolute paths, traversal, and another organization's prefix."""

    key = str(storage_key or "")
    org = str(organization_id)
    if not key or "\x00" in key or key.startswith(("/", "\\")):
        raise UnsafeSpatialPath("invalid storage key")
    parts = Path(key).parts
    if ".." in parts or any(part in {"", ".", ".."} for part in parts):
        raise UnsafeSpatialPath("invalid storage key")
    if len(parts) != 2 or parts[0] != org:
        raise UnsafeSpatialPath("storage key escapes organization")
    return key


def write_bytes(organization_id: str, storage_key: str, payload: bytes) -> None:
    key = assert_org_key(organization_id, storage_key)
    root = compute_root()
    destination = root / key
    destination.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    resolved = destination.resolve()
    if not resolved.is_relative_to(root):
        raise UnsafeSpatialPath("storage key escapes organization")
    fd = os.open(resolved, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(fd, payload)
    finally:
        os.close(fd)


def read_bytes(organization_id: str, storage_key: str) -> bytes:
    return resolve_compute_file(organization_id, storage_key).read_bytes()


def resolve_compute_file(organization_id: str, storage_key: str) -> Path:
    key = assert_org_key(organization_id, storage_key)
    root = compute_root()
    resolved = (root / key).resolve()
    if not resolved.is_relative_to(root):
        raise UnsafeSpatialPath("storage key escapes organization")
    if not resolved.is_file():
        raise UnsafeSpatialPath("file not found")
    return resolved


def file_exists(organization_id: str, storage_key: str) -> bool:
    try:
        resolve_compute_file(organization_id, storage_key)
    except UnsafeSpatialPath:
        return False
    return True


def remove_file(organization_id: str, storage_key: str) -> None:
    """Delete one organization file. A missing file is already gone."""

    try:
        resolve_compute_file(organization_id, storage_key).unlink()
    except UnsafeSpatialPath:
        return
