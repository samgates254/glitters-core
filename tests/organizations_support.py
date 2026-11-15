"""Helpers for organization tests. This module is not a test module."""

from __future__ import annotations

from django.utils import timezone
from rest_framework.test import APIClient

from apps.identity.models import User

ORGANIZATIONS = "/api/v1/organizations/"
ROLES = "/api/v1/organization-roles/"
ACCEPT = "/api/v1/invitations/accept/"
INSPECT = "/api/v1/invitations/inspect/"
MY_MEMBERSHIPS = "/api/v1/me/memberships/"


def verified_user(email: str, password: str, **extra) -> User:
    extra.setdefault("email_verified_at", timezone.now())
    return User.objects.create_user(email, password, **extra)


def client_for(user) -> APIClient:
    client = APIClient()
    client.force_login(user)
    return client


def organization_body(**overrides) -> dict:
    body = {
        "legal_name": "Acme Properties Limited",
        "display_name": "Acme Properties",
        "organization_type": "agency",
    }
    body.update(overrides)
    return body
