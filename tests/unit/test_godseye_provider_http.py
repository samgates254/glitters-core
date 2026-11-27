"""Redirect policy for provider reads. No network call."""

from urllib.request import Request

import pytest

from apps.spatial.services.providers.http import (
    ProviderTransportError,
    _AllowlistRedirect,
    allowlisted_redirect_url,
)

_HOSTS = frozenset({"stac.example"})


def _request(method: str = "GET", data: bytes | None = None) -> Request:
    return Request("https://stac.example/v1", data=data, method=method)


def test_same_host_slash_redirect_is_allowed():
    target = allowlisted_redirect_url(
        "https://stac.example/v1",
        "https://stac.example/v1/",
        _HOSTS,
    )
    assert target == "https://stac.example/v1/"


def test_relative_redirect_stays_on_the_current_host():
    target = allowlisted_redirect_url("https://stac.example/v1", "/v1/", _HOSTS)
    assert target == "https://stac.example/v1/"


def test_redirect_refuses_another_host_scheme_port_or_userinfo():
    refused = [
        "https://evil.example/v1/",
        "http://stac.example/v1/",
        "https://stac.example:8443/v1/",
        "https://user:secret@stac.example/v1/",
        "//evil.example/v1/",
        "",
    ]
    for location in refused:
        with pytest.raises(ProviderTransportError):
            allowlisted_redirect_url("https://stac.example/v1", location, _HOSTS)


def test_get_redirect_builds_a_get_for_the_slash_url():
    handler = _AllowlistRedirect(_HOSTS)
    follow = handler.redirect_request(
        _request(),
        None,
        307,
        "Temporary Redirect",
        {},
        "https://stac.example/v1/",
    )
    assert follow is not None
    assert follow.full_url == "https://stac.example/v1/"
    assert follow.get_method() == "GET"
    assert handler.max_redirections == 2


def test_post_307_keeps_the_body_and_post_302_is_refused():
    handler = _AllowlistRedirect(_HOSTS)
    body = b'{"limit":1}'
    follow = handler.redirect_request(
        _request("POST", body),
        None,
        307,
        "Temporary Redirect",
        {},
        "https://stac.example/v1/search/",
    )
    assert follow is not None
    assert follow.get_method() == "POST"
    assert follow.data == body
    with pytest.raises(ProviderTransportError):
        handler.redirect_request(
            _request("POST", body),
            None,
            302,
            "Found",
            {},
            "https://stac.example/v1/search/",
        )
