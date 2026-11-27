"""Compute providers. Commercial connectors have no HTTP client and no endpoint."""

from __future__ import annotations

import importlib.util
import shutil

from django.conf import settings

from apps.spatial.services.compute.contracts import (
    COMMERCIAL_PROVIDERS,
    COMMERCIAL_REASON,
    ENTERPRISE,
    LOCAL_CPU,
    LOCAL_GPU,
    MOCK_RENTED_GPU,
    REMOTE_RENDER_REASON,
    RUNPOD,
    VAST,
    ProviderNotConfigured,
    RemoteRenderDisabled,
)
from apps.spatial.services.compute.preflight import probe_gpu


def rental_enabled() -> bool:
    return bool(getattr(settings, "GLITTERS_GPU_RENTAL_ENABLED", False))


def remote_render_enabled() -> bool:
    return bool(getattr(settings, "GLITTERS_REMOTE_RENDER_ENABLED", False))


def commercial_refusal(provider_id: str, *, entitled: bool) -> dict:
    """Record a closed decision. Flag and entitlement still do not provision hardware."""

    if provider_id not in COMMERCIAL_PROVIDERS:
        raise ProviderNotConfigured("Not a commercial provider.")
    return {
        "provider_id": provider_id,
        "provision": False,
        "external_request_count": 0,
        "rental_enabled": rental_enabled(),
        "entitled": entitled,
        "endpoint": None,
        "reason": COMMERCIAL_REASON,
    }


def provider_catalog(*, entitled_ids: frozenset[str] | set[str] | None = None) -> list[dict]:
    entitled = set(entitled_ids or ())
    gpu = probe_gpu()
    rows = [
        {
            "id": LOCAL_CPU,
            "status": "available",
            "billable": False,
            "external_request_count": 0,
            "reason": "In-process CPU fixture pipeline.",
        },
        {
            "id": LOCAL_GPU,
            "status": gpu["status"],
            "billable": False,
            "vram_mb": gpu["vram_mb"],
            "external_request_count": 0,
            "fallback_provider_id": LOCAL_CPU,
            "reason": gpu["reason"] or "Local GPU probe succeeded. Reconstruction is not implied.",
        },
        {
            "id": MOCK_RENTED_GPU,
            "status": "simulator",
            "billable": False,
            "external_request_count": 0,
            "reason": "In-process simulator. Not Runpod, Vast.ai, or another rental platform.",
        },
    ]
    for provider_id in (RUNPOD, VAST, ENTERPRISE):
        decision = commercial_refusal(provider_id, entitled=provider_id in entitled)
        rows.append(
            {
                "id": provider_id,
                "status": "disabled",
                "billable": False,
                "external_request_count": decision["external_request_count"],
                "provision": False,
                "entitled": decision["entitled"],
                "rental_enabled": decision["rental_enabled"],
                "reason": decision["reason"],
            }
        )
    return rows


def adapter_status() -> list[dict]:
    """Report whether a tool binary or library exists. Nothing here is executed."""

    rows = []
    for name, command in (("OpenDroneMap/NodeODM", "nodeodm"), ("COLMAP", "colmap")):
        rows.append(
            {
                "name": name,
                "command": command,
                "present": shutil.which(command) is not None,
                "usable": False,
                "invoked": False,
                "reason": (
                    "Presence is not a completed reconstruction. "
                    "This adapter does not launch the tool."
                ),
            }
        )
    for module in ("torch", "onnxruntime"):
        rows.append(
            {
                "name": f"ML inference ({module})",
                "command": module,
                "present": importlib.util.find_spec(module) is not None,
                "usable": False,
                "invoked": False,
                "reason": "The framework was not imported and no model was executed.",
            }
        )
    return rows


def open_remote_render() -> None:
    """Interactive remote rendering stays closed even if the flag is set."""

    if remote_render_enabled():
        raise RemoteRenderDisabled(
            "The remote-render flag is on, but bandwidth, latency, GPU cost, and security "
            "have not been validated. No stream was opened."
        )
    raise RemoteRenderDisabled(REMOTE_RENDER_REASON)
