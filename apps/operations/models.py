"""Operational models for property management agreements and physical readiness."""

from __future__ import annotations

import builtins
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from apps.operations.domain import (
    AgreementStatus,
    OperationsEventAction,
    PhysicalState,
    generate_agreement_reference,
)
from apps.properties.domain import AccessStatus


class AppendOnlyQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValueError("Operations events are append-only.")

    def delete(self):
        raise ValueError("Operations events are append-only.")

    def bulk_update(self, objs, fields, batch_size=None):
        raise ValueError("Operations events are append-only.")


class ManagementAgreementQuerySet(models.QuerySet):
    """QuerySet for ManagementAgreement preventing unauthorized mutation of contractual authority."""

    def update(self, **kwargs):
        if "can_execute_leases" in kwargs:
            if self.exclude(status=AgreementStatus.DRAFT).exists():
                raise ValidationError(
                    "Direct update of 'can_execute_leases' is prohibited on accepted or active agreements. "
                    "Contractual terms are immutable."
                )
        if "lease_execution_revoked_at" in kwargs and kwargs["lease_execution_revoked_at"] is None:
            raise ValidationError(
                "Direct restoration of revoked authority via QuerySet.update() is prohibited. "
                "Restoration requires a fresh explicit agreement proposal and acceptance process."
            )
        return super().update(**kwargs)


class ManagementAgreement(models.Model):
    """Bilateral operational management agreement between a property owner and a PM organization."""

    objects = ManagementAgreementQuerySet.as_manager()

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    reference = models.CharField(
        max_length=32,
        unique=True,
        default=generate_agreement_reference,
        editable=False,
    )
    property = models.ForeignKey(
        "properties.Property",
        on_delete=models.PROTECT,
        related_name="management_agreements",
    )
    managing_organization = models.ForeignKey(
        "organizations.Organization",
        on_delete=models.PROTECT,
        related_name="management_agreements",
    )
    granting_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="granted_management_agreements",
        help_text="The verified owner who holds direct PropertyAccess(capability='manage').",
    )
    assigned_agent = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="assigned_management_agreements",
        help_text="The designated managing agent receiving operational property access.",
    )
    spend_limit_minor = models.BigIntegerField(
        default=1_000_000,
        help_text="Repair threshold in minor currency units before requiring explicit owner sign-off.",
    )
    commission_basis_points = models.PositiveIntegerField(
        default=800,
        help_text="Management fee percentage expressed in basis points (800 = 8.00%).",
    )
    valid_from = models.DateField()
    valid_until = models.DateField(null=True, blank=True)
    can_execute_leases = models.BooleanField(
        default=False,
        help_text="Explicit legal power of attorney to execute and terminate leases on behalf of the owner.",
    )
    lease_execution_revoked_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Timestamp when lease execution power was explicitly revoked by the owner.",
    )
    lease_execution_revoked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
        help_text="The verified owner who explicitly revoked lease execution authority.",
    )
    lease_execution_revocation_reason = models.TextField(
        blank=True,
        default="",
        help_text="Reason provided by the owner for revoking lease execution authority.",
    )
    status = models.CharField(
        max_length=32,
        choices=AgreementStatus.CHOICES,
        default=AgreementStatus.DRAFT,
    )
    accepted_by_owner_at = models.DateTimeField(null=True, blank=True)
    accepted_by_agent_at = models.DateTimeField(null=True, blank=True)
    activated_at = models.DateTimeField(null=True, blank=True)
    terminated_at = models.DateTimeField(null=True, blank=True)
    termination_reason = models.TextField(blank=True, default="")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="created_management_agreements",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "operations_management_agreement"
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(valid_until__isnull=True)
                    | models.Q(valid_until__gte=models.F("valid_from"))
                ),
                name="operations_agreement_valid_date_range",
            ),
            models.CheckConstraint(
                condition=models.Q(spend_limit_minor__gte=0),
                name="operations_agreement_spend_limit_non_negative",
            ),
            models.CheckConstraint(
                condition=models.Q(commission_basis_points__lte=10000),
                name="operations_agreement_commission_bp_range",
            ),
            models.CheckConstraint(
                condition=models.Q(status__in=sorted(AgreementStatus.VALUES)),
                name="operations_agreement_status_valid",
            ),
            models.UniqueConstraint(
                fields=["property"],
                condition=models.Q(status=AgreementStatus.ACTIVE),
                name="operations_one_active_agreement_per_property",
            ),
        ]
        indexes = [
            models.Index(fields=["property", "status"], name="operations_agr_prop_status_idx"),
            models.Index(
                fields=["managing_organization", "status"],
                name="operations_agr_org_status_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.reference} ({self.status}) - Property {self.property_id}"

    @builtins.property
    def has_effective_lease_execution_authority(self) -> bool:
        """Evaluate if the agreement currently confers active lease execution authority."""
        if not self.can_execute_leases:
            return False
        if self.status != AgreementStatus.ACTIVE:
            return False
        if self.lease_execution_revoked_at is not None:
            return False
        if self.valid_until is not None and self.valid_until < timezone.now().date():
            return False
        return True

    def save(self, *args, **kwargs):
        if not self._state.adding and self.pk:
            original = (
                ManagementAgreement.objects.filter(pk=self.pk)
                .values(
                    "status",
                    "can_execute_leases",
                    "lease_execution_revoked_at",
                )
                .first()
            )
            if original:
                orig_status = original["status"]
                if orig_status in {
                    AgreementStatus.ACCEPTED,
                    AgreementStatus.ACTIVE,
                    AgreementStatus.TERMINATED,
                    AgreementStatus.REVOKED,
                    AgreementStatus.EXPIRED,
                }:
                    if original["can_execute_leases"] != self.can_execute_leases:
                        raise ValidationError(
                            "can_execute_leases is an immutable contractual term and cannot be modified after agreement acceptance."
                        )
                # Clearing revocation timestamp to restore authority is prohibited
                if (
                    original["lease_execution_revoked_at"] is not None
                    and self.lease_execution_revoked_at is None
                ):
                    raise ValidationError(
                        "Revoked lease execution authority cannot be restored by clearing revocation timestamps. "
                        "Restoration requires a fresh explicit agreement proposal and acceptance process."
                    )
        super().save(*args, **kwargs)


class DelegatedPropertyAccess(models.Model):
    """Explicit link tracking operational access delegated through a ManagementAgreement."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    agreement = models.ForeignKey(
        ManagementAgreement,
        on_delete=models.CASCADE,
        related_name="delegated_access_grants",
    )
    property_access = models.ForeignKey(
        "properties.PropertyAccess",
        on_delete=models.CASCADE,
        related_name="operational_delegations",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="operational_delegated_access",
    )
    organization = models.ForeignKey(
        "organizations.Organization",
        on_delete=models.PROTECT,
        related_name="operational_delegated_access",
    )
    status = models.CharField(
        max_length=16,
        choices=AccessStatus.CHOICES,
        default=AccessStatus.ACTIVE,
    )
    expires_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "operations_delegated_access"
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(status__in=sorted(AccessStatus.VALUES)),
                name="operations_delegated_access_status_valid",
            ),
            models.UniqueConstraint(
                fields=["agreement", "user"],
                condition=models.Q(status=AccessStatus.ACTIVE),
                name="operations_delegated_access_one_active_per_user",
            ),
        ]

    def __str__(self) -> str:
        return f"Delegated access for {self.user_id} via {self.agreement.reference}"


class UnitPhysicalProfile(models.Model):
    """Physical condition, turnover, and readiness state of a unit (independent of contractual lease occupancy)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    property = models.OneToOneField(
        "properties.Property",
        on_delete=models.CASCADE,
        related_name="physical_profile",
    )
    physical_state = models.CharField(
        max_length=32,
        choices=PhysicalState.CHOICES,
        default=PhysicalState.TURNOVER_PREPARATION,
    )
    target_rent_minor = models.BigIntegerField(null=True, blank=True)
    target_currency = models.CharField(max_length=3, default="KES")
    ready_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True, default="")
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="updated_physical_profiles",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "operations_unit_physical_profile"
        ordering = ["-updated_at"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(physical_state__in=sorted(PhysicalState.VALUES)),
                name="operations_physical_state_valid",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(target_rent_minor__isnull=True)
                    | models.Q(target_rent_minor__gte=0)
                ),
                name="operations_target_rent_non_negative",
            ),
        ]

    def __str__(self) -> str:
        return f"PhysicalProfile: Property {self.property_id} ({self.physical_state})"


class OperationsEvent(models.Model):
    """Immutable audit record for operational state changes and management agreements."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    idempotency_key = models.CharField(max_length=64, unique=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="operations_events",
    )
    property = models.ForeignKey(
        "properties.Property",
        on_delete=models.CASCADE,
        related_name="operations_events",
    )
    organization = models.ForeignKey(
        "organizations.Organization",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="operations_events",
    )
    action = models.CharField(max_length=64, choices=OperationsEventAction.CHOICES)
    object_type = models.CharField(max_length=64)
    object_id = models.CharField(max_length=36)
    request_id = models.CharField(max_length=128, blank=True, default="")
    metadata = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = AppendOnlyQuerySet.as_manager()

    class Meta:
        db_table = "operations_event"
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(action__in=sorted(OperationsEventAction.VALUES)),
                name="operations_event_action_valid",
            ),
        ]
        indexes = [
            models.Index(fields=["property", "created_at"], name="operations_event_prop_idx"),
            models.Index(fields=["organization", "created_at"], name="operations_event_org_idx"),
        ]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValueError("Operations events are append-only.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError("Operations events are append-only.")
