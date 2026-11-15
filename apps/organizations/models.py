"""Organization tenancy.

A person is `identity.User`. Authority inside a business is a membership row.
The user record has no organization role. A registration reference is a label
supplied by the organization. Storing one does not verify that the business
exists or that the number was issued by a registrar.
"""

from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models

from apps.organizations.domain import (
    COUNTRY_RE,
    SLUG_RE,
    EventAction,
    InvitationStatus,
    MembershipSource,
    MembershipStatus,
    OrganizationStatus,
    OrganizationType,
    Role,
)


class Organization(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    legal_name = models.CharField(max_length=200)
    display_name = models.CharField(max_length=200)
    slug = models.SlugField(max_length=80, unique=True)
    organization_type = models.CharField(max_length=32, choices=OrganizationType.CHOICES)
    registration_reference = models.CharField(max_length=64, blank=True, default="")
    contact_email = models.EmailField(blank=True, default="")
    contact_phone = models.CharField(max_length=16, blank=True, default="")
    website = models.URLField(blank=True, default="")
    country = models.CharField(max_length=2, default="KE")
    region = models.CharField(max_length=80, blank=True, default="")
    address_line = models.CharField(max_length=255, blank=True, default="")
    status = models.CharField(
        max_length=32,
        choices=OrganizationStatus.CHOICES,
        default=OrganizationStatus.ACTIVE,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "organization"
        ordering = ["display_name"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(status__in=sorted(OrganizationStatus.VALUES)),
                name="organization_status_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(organization_type__in=sorted(OrganizationType.VALUES)),
                name="organization_type_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(slug__regex=SLUG_RE.pattern),
                name="organization_slug_shape",
            ),
            models.CheckConstraint(
                condition=models.Q(country__regex=COUNTRY_RE.pattern),
                name="organization_country_iso",
            ),
            models.CheckConstraint(
                condition=models.Q(contact_phone="")
                | models.Q(contact_phone__regex=r"^\+[1-9]\d{7,14}$"),
                name="organization_contact_phone_e164",
            ),
            models.CheckConstraint(
                condition=~models.Q(legal_name="") & ~models.Q(display_name=""),
                name="organization_names_present",
            ),
        ]
        indexes = [
            models.Index(fields=["status"], name="organization_status_idx"),
        ]

    def __str__(self) -> str:
        return self.display_name


class OrganizationMembership(models.Model):
    """One row per user and organization. Status carries the lifecycle."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="memberships",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="organization_memberships",
    )
    role = models.CharField(max_length=16, choices=Role.CHOICES)
    status = models.CharField(max_length=16, choices=MembershipStatus.CHOICES)
    source = models.CharField(max_length=16, choices=MembershipSource.CHOICES)
    joined_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "organization_membership"
        ordering = ["created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "user"],
                name="organization_membership_user_unique",
            ),
            models.UniqueConstraint(
                fields=["organization"],
                condition=models.Q(role=Role.OWNER, status=MembershipStatus.ACTIVE),
                name="organization_one_active_owner",
            ),
            models.CheckConstraint(
                condition=models.Q(role__in=sorted(Role.VALUES)),
                name="organization_membership_role_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(status__in=sorted(MembershipStatus.VALUES)),
                name="organization_membership_status_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(source__in=sorted(MembershipSource.VALUES)),
                name="organization_membership_source_valid",
            ),
            models.CheckConstraint(
                condition=(
                    ~models.Q(status=MembershipStatus.ACTIVE) | models.Q(joined_at__isnull=False)
                ),
                name="organization_membership_joined_when_active",
            ),
        ]
        indexes = [
            models.Index(fields=["organization", "status"], name="org_membership_org_status"),
            models.Index(fields=["user", "status"], name="org_membership_user_status"),
        ]

    def __str__(self) -> str:
        return f"{self.user_id} {self.role} {self.organization_id}"


class OrganizationInvitation(models.Model):
    """One-time invitation. Only the SHA-256 hash of the token is stored."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="invitations",
    )
    email = models.EmailField()
    role = models.CharField(max_length=16, choices=Role.CHOICES)
    invited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="organization_invitations_sent",
    )
    token_hash = models.CharField(max_length=64, unique=True)
    expires_at = models.DateTimeField()
    accepted_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(
        max_length=16,
        choices=InvitationStatus.CHOICES,
        default=InvitationStatus.PENDING,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "organization_invitation"
        ordering = ["created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "email"],
                condition=models.Q(status=InvitationStatus.PENDING),
                name="organization_invitation_one_pending",
            ),
            models.CheckConstraint(
                condition=~models.Q(email=""),
                name="organization_invitation_email_present",
            ),
            models.CheckConstraint(
                condition=~models.Q(role=Role.OWNER),
                name="organization_invitation_role_not_owner",
            ),
            models.CheckConstraint(
                condition=models.Q(role__in=sorted(Role.VALUES - {Role.OWNER})),
                name="organization_invitation_role_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(status__in=sorted(InvitationStatus.VALUES)),
                name="organization_invitation_status_valid",
            ),
        ]
        indexes = [
            models.Index(fields=["organization", "status"], name="org_invitation_org_status"),
        ]

    def __str__(self) -> str:
        return f"{self.email} {self.organization_id} {self.status}"


class AppendOnlyQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValueError("Organization events are append-only.")

    def delete(self):
        raise ValueError("Organization events are append-only.")


class OrganizationEvent(models.Model):
    """Append-only organization audit. Metadata must not contain secrets."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="organization_events",
    )
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="events",
    )
    action = models.CharField(max_length=64, choices=EventAction.CHOICES)
    object_type = models.CharField(max_length=64)
    object_id = models.CharField(max_length=36)
    request_id = models.CharField(max_length=128, blank=True, default="")
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    metadata = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = AppendOnlyQuerySet.as_manager()

    class Meta:
        db_table = "organization_event"
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(action__in=sorted(EventAction.VALUES)),
                name="organization_event_action_valid",
            ),
        ]
        indexes = [
            models.Index(fields=["organization", "created_at"], name="org_event_org_created"),
        ]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValueError("Organization events are append-only.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError("Organization events are append-only.")

    def __str__(self) -> str:
        return f"{self.action} {self.organization_id}"
