from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models
from django.db.models import F, Q
from django.utils import timezone

from apps.backoffice.domain import AccessScope, RoleEventAction, StaffRole


class ImmutableRoleEventQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValueError("Backoffice role audit events are immutable.")

    def delete(self):
        raise ValueError("Backoffice role audit events are immutable.")


class StaffRoleGrantQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValueError("Backoffice role grants must be revoked through the service.")

    def delete(self):
        raise ValueError("Backoffice role grants are historical records and cannot be deleted.")


class StaffRoleGrant(models.Model):
    objects = StaffRoleGrantQuerySet.as_manager()

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="backoffice_role_grants",
    )
    role = models.CharField(max_length=40, choices=StaffRole.CHOICES)
    scope = models.CharField(max_length=16, choices=AccessScope.CHOICES)
    organization = models.ForeignKey(
        "organizations.Organization",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="backoffice_role_grants",
    )
    granted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="backoffice_grants_issued",
    )
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    expires_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True, editable=False)
    revoked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="backoffice_grants_revoked",
    )

    class Meta:
        ordering = ["-created_at", "id"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(role__in=sorted(StaffRole.VALUES)),
                name="backoffice_grant_role_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(scope__in=sorted(AccessScope.VALUES)),
                name="backoffice_grant_scope_valid",
            ),
            models.CheckConstraint(
                condition=(
                    Q(scope=AccessScope.PLATFORM, organization__isnull=True)
                    | Q(scope=AccessScope.ORGANIZATION, organization__isnull=False)
                ),
                name="backoffice_grant_scope_org_shape",
            ),
            models.CheckConstraint(
                condition=Q(expires_at__isnull=True) | Q(expires_at__gt=F("created_at")),
                name="backoffice_grant_expiry_after_create",
            ),
            models.CheckConstraint(
                condition=(
                    Q(revoked_at__isnull=True, revoked_by__isnull=True)
                    | Q(revoked_at__isnull=False, revoked_by__isnull=False)
                ),
                name="backoffice_grant_revocation_pair",
            ),
        ]
        indexes = [
            models.Index(
                fields=["user", "revoked_at", "expires_at"],
                name="backoffice_grant_user_active",
            ),
            models.Index(
                fields=["organization", "role", "revoked_at"],
                name="backoffice_grant_org_role",
            ),
        ]

    @property
    def is_active(self):
        return self.revoked_at is None and (
            self.expires_at is None or self.expires_at > timezone.now()
        )

    def save(self, *args, **kwargs):
        if self._state.adding:
            if self.revoked_at is not None or self.revoked_by_id is not None:
                raise ValueError("A role grant must be created active.")
            return super().save(*args, **kwargs)

        previous = type(self).objects.get(pk=self.pk)
        immutable_fields = ("user_id", "role", "scope", "organization_id", "granted_by_id")
        if any(getattr(previous, field) != getattr(self, field) for field in immutable_fields):
            raise ValueError("A role grant's identity and scope are immutable.")
        if previous.expires_at != self.expires_at:
            raise ValueError("A role grant's expiry cannot be rewritten.")
        if previous.revoked_at is not None:
            raise ValueError("A revoked role grant cannot be changed.")
        if self.revoked_at is None or self.revoked_by_id is None:
            raise ValueError("Use the role revocation service to revoke a grant.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError("Backoffice role grants are historical records and cannot be deleted.")


class StaffRoleEvent(models.Model):
    objects = ImmutableRoleEventQuerySet.as_manager()

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    grant = models.ForeignKey(
        StaffRoleGrant,
        on_delete=models.PROTECT,
        related_name="events",
    )
    action = models.CharField(max_length=16, choices=RoleEventAction.CHOICES)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="backoffice_role_events",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["created_at", "id"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(action__in=sorted(RoleEventAction.VALUES)),
                name="backoffice_role_event_action_valid",
            )
        ]
        indexes = [models.Index(fields=["grant", "created_at"], name="backoffice_event_grant_time")]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValueError("Backoffice role audit events are immutable.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError("Backoffice role audit events are immutable.")
