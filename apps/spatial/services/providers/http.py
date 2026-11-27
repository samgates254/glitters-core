"""Bounded HTTPS reads.

Unlisted hosts are refused. A redirect is followed only when the next URL stays
on an allow-listed HTTPS host and the same port, for at most two hops.
"""

from __future__ import annotations

import json
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse, urlunparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

USER_AGENT = "glitters-godseye/1.0 (bounded provider check)"
_MAX_REDIRECTS = 2
_RETRYABLE = frozenset(
    {
        "The provider timed out.",
        "The provider could not be reached.",
    }
)


class ProviderTransportError(Exception):
    def __init__(self, message: str, http_status: int | None = None):
        super().__init__(message)
        self.message = message
        self.http_status = http_status


def _port(parsed) -> int | None:
    if parsed.port is not None:
        return parsed.port
    if parsed.scheme == "https":
        return 443
    return None


def allowlisted_redirect_url(
    current_url: str,
    location: str,
    allowed_hosts: frozenset[str],
) -> str:
    """Resolve one redirect and refuse anything off the provider host."""

    if not isinstance(location, str) or not location.strip():
        raise ProviderTransportError("The provider redirect had no location.")
    target = urljoin(current_url, location.strip())
    current = urlparse(current_url)
    parsed = urlparse(target)
    host = (parsed.hostname or "").lower()
    allowed = {item.lower() for item in allowed_hosts}
    if (
        parsed.scheme != "https"
        or host not in allowed
        or parsed.username
        or parsed.password
        or _port(parsed) != _port(current)
    ):
        raise ProviderTransportError("The provider redirected off the allow list.")
    if parsed.fragment:
        target = urlunparse(parsed._replace(fragment=""))
    return target


class _AllowlistRedirect(HTTPRedirectHandler):
    """Follow a few same-host redirects. POST is preserved only for 307 and 308."""

    max_repeats = 1
    max_redirections = _MAX_REDIRECTS

    def __init__(self, allowed_hosts: frozenset[str]):
        super().__init__()
        self.allowed_hosts = allowed_hosts

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if code not in (301, 302, 303, 307, 308):
            raise ProviderTransportError(f"The provider redirected the request ({code}).", code)
        method = req.get_method()
        if method not in ("GET", "HEAD", "POST"):
            raise ProviderTransportError(f"The provider redirected the request ({code}).", code)
        if code in (301, 302, 303) and method == "POST":
            raise ProviderTransportError(f"The provider redirected a POST request ({code}).", code)
        target = allowlisted_redirect_url(req.full_url, newurl, self.allowed_hosts)
        kept = {key: value for key, value in req.header_items() if key.lower() != "content-length"}
        if code in (307, 308):
            return Request(  # noqa: S310
                target,
                data=req.data,
                headers=kept,
                origin_req_host=req.origin_req_host,
                unverifiable=True,
                method=method,
            )
        kept = {key: value for key, value in kept.items() if key.lower() != "content-type"}
        return Request(  # noqa: S310
            target,
            headers=kept,
            origin_req_host=req.origin_req_host,
            unverifiable=True,
            method="HEAD" if method == "HEAD" else "GET",
        )


def _assert_allowed(url: str, allowed_hosts: frozenset[str]) -> None:
    parsed = urlparse(url)
    host = parsed.hostname or ""
    if parsed.scheme != "https" or host not in allowed_hosts or parsed.username or parsed.password:
        raise ProviderTransportError("The provider URL is not on the allow list.")


def _read_limited(response, max_bytes: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        block = response.read(65536)
        if not block:
            break
        total += len(block)
        if total > max_bytes:
            raise ProviderTransportError("The provider response exceeded the read limit.")
        chunks.append(block)
    return b"".join(chunks)


def _open_https(
    url: str,
    *,
    allowed_hosts: frozenset[str],
    max_bytes: int,
    timeout: float,
    method: str,
    body: bytes | None,
    content_type: str | None,
) -> tuple[int, bytes]:
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json, application/xml, */*"}
    if content_type:
        headers["Content-Type"] = content_type
    request = Request(url, data=body, headers=headers, method=method)  # noqa: S310
    opener = build_opener(_AllowlistRedirect(allowed_hosts))
    try:
        with opener.open(request, timeout=timeout) as response:  # noqa: S310
            status = int(getattr(response, "status", 200))
            return status, _read_limited(response, max_bytes)
    except ProviderTransportError:
        raise
    except HTTPError as exc:
        detail = _error_detail(_read_limited(exc, max_bytes))
        message = f"The provider returned HTTP {exc.code}."
        if detail:
            message = f"{message} {detail}"
        raise ProviderTransportError(message, exc.code) from None
    except TimeoutError:
        raise ProviderTransportError("The provider timed out.") from None
    except URLError:
        raise ProviderTransportError("The provider could not be reached.") from None


def fetch_https(
    url: str,
    *,
    allowed_hosts: frozenset[str],
    max_bytes: int,
    timeout: float = 8.0,
    method: str = "GET",
    body: bytes | None = None,
    content_type: str | None = None,
) -> tuple[int, bytes]:
    _assert_allowed(url, allowed_hosts)
    attempts = 2 if method in ("GET", "HEAD") else 1
    for attempt in range(attempts):
        try:
            return _open_https(
                url,
                allowed_hosts=allowed_hosts,
                max_bytes=max_bytes,
                timeout=timeout,
                method=method,
                body=body,
                content_type=content_type,
            )
        except ProviderTransportError as exc:
            if attempt + 1 == attempts or exc.message not in _RETRYABLE:
                raise
    raise ProviderTransportError("The provider could not be reached.")


def _error_detail(payload: bytes) -> str:
    try:
        document = json.loads(payload.decode("utf-8"))
    except UnicodeDecodeError, json.JSONDecodeError:
        return ""
    if not isinstance(document, dict):
        return ""
    text = document.get("description") or document.get("detail") or ""
    if not isinstance(text, str):
        return ""
    return " ".join(text.split())[:180]


def decode_json(payload: bytes):
    try:
        return json.loads(payload.decode("utf-8"))
    except UnicodeDecodeError, json.JSONDecodeError:
        raise ProviderTransportError("The provider response was not JSON.") from None
