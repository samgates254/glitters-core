"""Disabled commercial connectors make no billable request."""

import http.client
import urllib.request
from pathlib import Path

import pytest

from apps.spatial.services.compute.contracts import RemoteRenderDisabled
from apps.spatial.services.compute.providers import (
    adapter_status,
    commercial_refusal,
    open_remote_render,
    provider_catalog,
)

BILLING_HOSTS = (
    "api.runpod.io",
    "rest.runpod.io",
    "console.vast.ai",
    "vast.ai/api",
    "api.vast.ai",
)


def _refuse_network(monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("external request")

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    monkeypatch.setattr(http.client, "HTTPSConnection", boom)
    monkeypatch.setattr(http.client, "HTTPConnection", boom)


@pytest.mark.parametrize("provider_id", ["runpod", "vast", "enterprise"])
@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("entitled", [False, True])
def test_commercial_connector_never_provisions(
    monkeypatch, settings, provider_id, enabled, entitled
):
    _refuse_network(monkeypatch)
    settings.GLITTERS_GPU_RENTAL_ENABLED = enabled
    decision = commercial_refusal(provider_id, entitled=entitled)
    assert decision["provision"] is False
    assert decision["external_request_count"] == 0
    assert decision["endpoint"] is None
    catalog = provider_catalog(entitled_ids={provider_id} if entitled else set())
    row = next(item for item in catalog if item["id"] == provider_id)
    assert row["status"] == "disabled"
    assert row["billable"] is False
    assert row["external_request_count"] == 0
    assert row["provision"] is False


def test_provider_module_has_no_billing_endpoint():
    source = Path(__file__).resolve().parents[2] / "apps/spatial/services/compute/providers.py"
    text = source.read_text(encoding="utf-8")
    for host in BILLING_HOSTS:
        assert host not in text


def test_adapters_are_not_invoked(monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("adapter invoked a process")

    monkeypatch.setattr("apps.spatial.services.compute.preflight.subprocess.run", boom)
    rows = adapter_status()
    assert rows
    assert all(row["usable"] is False and row["invoked"] is False for row in rows)


def test_remote_render_stays_closed(settings):
    settings.GLITTERS_REMOTE_RENDER_ENABLED = False
    with pytest.raises(RemoteRenderDisabled):
        open_remote_render()
    settings.GLITTERS_REMOTE_RENDER_ENABLED = True
    with pytest.raises(RemoteRenderDisabled):
        open_remote_render()
