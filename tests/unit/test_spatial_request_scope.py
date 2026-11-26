"""Retest the Wave 0 organization bind when the caller has not selected one org."""

from __future__ import annotations

import uuid

import pytest
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from tests.organizations_support import verified_user

from apps.organizations.models import Organization, OrganizationMembership
from apps.spatial.services.request_scope import bind_member_organization

pytestmark = pytest.mark.django_db


class _Request:
    def __init__(self, user):
        self.user = user
        self.query_params = {}
        self.headers = {}


class _Serializer:
    def __init__(self):
        self.validated_data = {}
        self.saved = None

    def save(self, **kwargs):
        self.saved = kwargs


def _organization(name: str) -> Organization:
    return Organization.objects.create(
        legal_name=f"{name} Ltd",
        display_name=name,
        slug=f"{name.lower()}-{uuid.uuid4().hex[:6]}",
        organization_type="agency",
        status="active",
        country="KE",
    )


def _join(user, organization) -> None:
    OrganizationMembership.objects.create(
        organization=organization,
        user=user,
        role="member",
        status="active",
        source="creator",
        joined_at=timezone.now(),
    )


def test_bind_requires_one_organization_when_membership_is_ambiguous() -> None:
    user = verified_user(f"scope-{uuid.uuid4().hex[:8]}@example.com", "Password-123!")
    with pytest.raises(ValidationError) as missing:
        bind_member_organization(_Request(user), _Serializer())
    assert str(missing.value.detail["organization_id"]) == "Select one of your organizations."

    _join(user, _organization("alpha"))
    _join(user, _organization("beta"))
    with pytest.raises(ValidationError) as ambiguous:
        bind_member_organization(_Request(user), _Serializer())
    assert str(ambiguous.value.detail["organization_id"]) == "Select one of your organizations."


def test_bind_uses_the_only_membership() -> None:
    user = verified_user(f"scope-{uuid.uuid4().hex[:8]}@example.com", "Password-123!")
    organization = _organization("solo")
    _join(user, organization)
    serializer = _Serializer()

    bound = bind_member_organization(_Request(user), serializer)

    assert bound.id == organization.id
    assert serializer.saved["organization"].id == organization.id
    assert serializer.saved["created_by"].id == user.id
