"""Throttles for public marketplace writes."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping

from django.conf import settings
from rest_framework.throttling import SimpleRateThrottle

from apps.identity.domain import normalize_email


class EnquiryThrottle(SimpleRateThrottle):
    def allow_request(self, request, view):
        if not getattr(settings, "ENQUIRY_THROTTLE", True):
            return True
        return super().allow_request(request, view)


class EnquiryCreationThrottle(EnquiryThrottle):
    """Limit one address and one contact email together.

    An address-only bucket is a separate throttle. Neither bucket stores the
    email or the message.
    """

    scope = "enquiry_create"

    def get_cache_key(self, request, view):
        ident = self.get_ident(request) or "unknown"
        data = request.data if isinstance(getattr(request, "data", None), Mapping) else {}
        raw = data.get("contact_email", "")
        if not isinstance(raw, str):
            raw = ""
        digest = hashlib.sha256(
            normalize_email(raw).encode("utf-8"),
            usedforsecurity=False,
        ).hexdigest()[:12]
        return f"throttle_{self.scope}_{ident}_{digest}"


class EnquiryAddressThrottle(EnquiryThrottle):
    """Limit enquiry posts from one address when the email keeps changing."""

    scope = "enquiry_create_address"

    def get_cache_key(self, request, view):
        ident = self.get_ident(request) or "unknown"
        return self.cache_format % {"scope": self.scope, "ident": ident}
