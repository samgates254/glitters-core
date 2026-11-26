"""Tenancy domain models for contractual leases, obligations, signatures, and auditing."""

from __future__ import annotations

import uuid

from django.conf import settings
from django.contrib.postgres.constraints import ExclusionConstraint
from django.contrib.postgres.fields import RangeOperators
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models.expressions import F, Func, Value

from apps.properties.domain import HierarchyRole
from apps.tenancy.domain import (
    INVALID_UNIT,
    STATUS_INVALID,
    AmendmentStatus,
    AmendmentType,
    ApplicationStatus,
    DeductionDisputeStatus,
    DeductionType,
    DepositStatus,
    EmploymentStatus,
    HandoverStatus,
    HandoverType,
    IdDocumentType,
    LeasePartyRole,
    LeaseStatus,
    LeaseType,
    PartySignatureStatus,
    ScheduleStatus,
    SignatureMethod,
    TenancyEventAction,
    generate_amendment_reference,
    generate_application_reference,
    generate_lease_reference,
    lease_transition_allowed,
)
from apps.tenancy.services.document import (
    CONTRACT_DATA_HASH_HELP,
    RENDERED_DOCUMENT_HASH_HELP,
)


class DateRange(Func):
    function = "daterange"
    output_field = models.CharField()


class AppendOnlyQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValueError("Tenancy events are append-only and cannot be updated.")

    def delete(self):
        raise ValueError("Tenancy events are append-only and cannot be deleted.")

    def bulk_update(self, objs, fields, batch_size=None):
        raise ValueError("Tenancy events are append-only and cannot be updated in bulk.")


class RentalApplication(models.Model):
    """Prospective tenant screening application prior to contractual lease generation."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    reference = models.CharField(
        max_length=32,
        unique=True,
        default=generate_application_reference,
        editable=False,
    )
    property = models.ForeignKey(
        "properties.Property",
        on_delete=models.PROTECT,
        related_name="rental_applications",
    )
    applicant = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="rental_applications",
    )
    proposed_start_date = models.DateField()
    proposed_term_months = models.PositiveSmallIntegerField(default=12)
    monthly_income_minor = models.BigIntegerField()
    income_currency = models.CharField(max_length=3, default="KES")
    employment_status = models.CharField(max_length=32, choices=EmploymentStatus.CHOICES)
    employer_name = models.CharField(max_length=200, blank=True, default="")
    id_document_type = models.CharField(max_length=32, choices=IdDocumentType.CHOICES)
    id_document_number = models.CharField(max_length=64)
    status = models.CharField(
        max_length=32,
        choices=ApplicationStatus.CHOICES,
        default=ApplicationStatus.SUBMITTED,
    )
    vetted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="vetted_rental_applications",
    )
    vetted_at = models.DateTimeField(null=True, blank=True)
    decision_notes = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "tenancy_rental_application"
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(monthly_income_minor__gte=0),
                name="tenancy_app_monthly_income_non_neg",
            ),
            models.CheckConstraint(
                condition=models.Q(proposed_term_months__gte=1, proposed_term_months__lte=120),
                name="tenancy_app_term_months_range",
            ),
            models.CheckConstraint(
                condition=models.Q(status__in=sorted(ApplicationStatus.VALUES)),
                name="tenancy_app_status_valid",
            ),
        ]
        indexes = [
            models.Index(fields=["property", "status"], name="tenancy_app_prop_status_idx"),
            models.Index(fields=["applicant", "status"], name="tenancy_app_user_status_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.reference} ({self.status}) - Property {self.property_id}"


IMMUTABLE_CONTRACT_FIELDS = {
    "property_id",
    "property_owner_id",
    "managing_organization_id",
    "lease_type",
    "start_date",
    "term_end_date",
    "rent_amount_minor",
    "rent_currency",
    "deposit_amount_minor",
    "service_charge_minor",
    "payment_due_day",
    "grace_period_days",
    "special_covenants",
    "contract_data_hash",
    "rendered_document_hash",
    "created_by_id",
}

# Frozen while a signature reservation is pending. effective_end_date stays
# editable after execution so termination can release the occupancy tail.
PENDING_RESERVATION_FIELDS = frozenset({"effective_end_date", "signature_deadline"})

_LEASE_UPDATE_ALIASES = {
    "property": "property_id",
    "property_owner": "property_owner_id",
    "managing_organization": "managing_organization_id",
    "created_by": "created_by_id",
}


def _lease_update_field_names(fields) -> set[str]:
    """Normalize FK aliases so QuerySet.update(property=...) hits the immutability guard."""
    return {_LEASE_UPDATE_ALIASES.get(field, field) for field in fields}


def _reject_lease_status_changes(current_statuses, proposed: str) -> None:
    for current in current_statuses:
        if not lease_transition_allowed(current, proposed):
            raise ValidationError(STATUS_INVALID)


def _reject_pending_contract_writes(statuses, fields: set[str]) -> None:
    """Block contractual rewrites after terms are approved and before execution."""

    if LeaseStatus.PENDING_SIGNATURE not in statuses:
        return
    frozen = fields.intersection(IMMUTABLE_CONTRACT_FIELDS | PENDING_RESERVATION_FIELDS)
    if not frozen:
        return
    label = ", ".join(sorted(frozen))
    raise ValidationError(
        "Direct update of contractual fields "
        f"[{label}] is prohibited once lease terms are approved."
    )


class LeaseQuerySet(models.QuerySet):
    """QuerySet for Lease enforcing contractual immutability on executed records."""

    def update(self, **kwargs):
        normalized = _lease_update_field_names(kwargs.keys())
        attempted_immutable = normalized.intersection(IMMUTABLE_CONTRACT_FIELDS)
        attempted_pending = normalized.intersection(PENDING_RESERVATION_FIELDS)
        needs_status = "status" in kwargs or bool(attempted_immutable or attempted_pending)
        current_statuses = []
        if needs_status:
            current_statuses = list(self.values_list("status", flat=True).distinct())
        if "status" in kwargs:
            _reject_lease_status_changes(current_statuses, kwargs["status"])
        if attempted_immutable and any(
            status in LeaseStatus.EXECUTED for status in current_statuses
        ):
            raise ValidationError(
                f"Direct update of contractual fields {sorted(attempted_immutable)} is prohibited on "
                "executed leases. Formal contractual variations must use LeaseAmendment."
            )
        _reject_pending_contract_writes(current_statuses, normalized)
        return super().update(**kwargs)

    def bulk_update(self, objs, fields, **kwargs):
        normalized = _lease_update_field_names(fields)
        attempted_immutable = normalized.intersection(IMMUTABLE_CONTRACT_FIELDS)
        attempted_pending = normalized.intersection(PENDING_RESERVATION_FIELDS)
        if attempted_immutable or attempted_pending or "status" in fields:
            current = {
                row["id"]: row["status"]
                for row in Lease.objects.filter(pk__in=[obj.pk for obj in objs if obj.pk]).values(
                    "id", "status"
                )
            }
            for obj in objs:
                previous = current.get(obj.pk)
                if previous is None:
                    continue
                if "status" in fields and not lease_transition_allowed(previous, obj.status):
                    raise ValidationError(STATUS_INVALID)
                if attempted_immutable and previous in LeaseStatus.EXECUTED:
                    fields_label = sorted(attempted_immutable)
                    raise ValidationError(
                        f"Direct bulk_update of contractual fields {fields_label} is prohibited on "
                        "executed leases. Formal contractual variations must use LeaseAmendment."
                    )
                if previous == LeaseStatus.PENDING_SIGNATURE and (
                    attempted_immutable or attempted_pending
                ):
                    watched = attempted_immutable | attempted_pending
                    label = ", ".join(sorted(watched))
                    raise ValidationError(
                        "Direct bulk_update of contractual fields "
                        f"[{label}] is prohibited once lease terms are approved."
                    )
        return super().bulk_update(objs, fields, **kwargs)

    def bulk_create(self, objs, *args, **kwargs):
        from apps.tenancy.services.validation import validate_rentable_unit
        for obj in objs:
            if obj.property_id:
                validate_rentable_unit(obj.property)
        return super().bulk_create(objs, *args, **kwargs)

    def delete(self):
        if self.filter(status__in=LeaseStatus.EXECUTED).exists():
            raise ValidationError(
                "Executed leases cannot be deleted via bulk QuerySet.delete(). Use formal termination."
            )
        return super().delete()


class Lease(models.Model):
    """Authoritative bilateral contractual agreement between landlord and tenant."""

    objects = LeaseQuerySet.as_manager()

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    reference = models.CharField(
        max_length=32,
        unique=True,
        default=generate_lease_reference,
        editable=False,
    )
    property = models.ForeignKey(
        "properties.Property",
        on_delete=models.PROTECT,
        related_name="leases",
    )
    property_owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="owned_leases",
        help_text="The verified owner who holds direct PropertyAccess(capability='manage').",
    )
    managing_organization = models.ForeignKey(
        "organizations.Organization",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="managed_leases",
    )
    management_agreement = models.ForeignKey(
        "operations.ManagementAgreement",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="leases",
    )
    application = models.ForeignKey(
        RentalApplication,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="resulting_leases",
    )
    lease_type = models.CharField(
        max_length=32,
        choices=LeaseType.CHOICES,
        default=LeaseType.RESIDENTIAL_FIXED,
    )
    start_date = models.DateField(
        help_text="Inclusive commencement date [start_date, effective_end_date)."
    )
    term_end_date = models.DateField(
        null=True,
        blank=True,
        help_text="Contractual stated term expiration date in the lease document.",
    )
    effective_end_date = models.DateField(
        null=True,
        blank=True,
        help_text="Exclusive upper bound date for the PostgreSQL GiST daterange [start_date, effective_end_date).",
    )
    actual_termination_date = models.DateField(
        null=True,
        blank=True,
        help_text="Date on which contractual tenancy formally ceased (differs in early termination).",
    )
    actual_handover_date = models.DateField(
        null=True,
        blank=True,
        help_text="Physical date keys were returned via move-out HandoverProtocol.",
    )
    rent_amount_minor = models.BigIntegerField(
        help_text="Base rent per billing cycle in integer minor currency units."
    )
    rent_currency = models.CharField(max_length=3, default="KES")
    deposit_amount_minor = models.BigIntegerField(
        default=0,
        help_text="Required security deposit obligation in integer minor currency units.",
    )
    service_charge_minor = models.BigIntegerField(
        default=0,
        help_text="Contractual service charge per cycle in integer minor currency units.",
    )
    payment_due_day = models.PositiveSmallIntegerField(
        default=1,
        help_text="Calendar day of the month when rent is due (1 to 28).",
    )
    grace_period_days = models.PositiveSmallIntegerField(
        default=5,
        help_text="Contractual grace days before late penalties apply.",
    )
    signature_deadline = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Expiry timestamp for pending lease signature reservation.",
    )
    document_version = models.PositiveIntegerField(
        default=1,
        help_text="Monotonically increasing version of the canonical contract terms.",
    )
    contract_data_hash = models.CharField(
        max_length=64,
        blank=True,
        default="",
        help_text=CONTRACT_DATA_HASH_HELP,
    )
    rendered_document_hash = models.CharField(
        max_length=64,
        blank=True,
        default="",
        help_text=RENDERED_DOCUMENT_HASH_HELP,
    )
    rendered_document_uri = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="Immutable document storage URI.",
    )
    special_covenants = models.TextField(
        blank=True,
        default="",
        help_text="Verbatim agreed special conditions or customized contractual clauses.",
    )
    status = models.CharField(
        max_length=32,
        choices=LeaseStatus.CHOICES,
        default=LeaseStatus.DRAFT,
    )
    signed_at = models.DateTimeField(null=True, blank=True)
    activated_at = models.DateTimeField(null=True, blank=True)
    terminated_at = models.DateTimeField(null=True, blank=True)
    termination_reason = models.TextField(blank=True, default="")
    supersedes_lease = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="renewed_by",
        help_text="The preceding lease being renewed by this replacement agreement.",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="created_leases",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "tenancy_lease"
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(effective_end_date__isnull=True)
                    | models.Q(effective_end_date__gt=models.F("start_date"))
                ),
                name="tenancy_lease_effective_end_after_start",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(term_end_date__isnull=True)
                    | models.Q(term_end_date__gte=models.F("start_date"))
                ),
                name="tenancy_lease_term_end_gte_start",
            ),
            models.CheckConstraint(
                condition=models.Q(rent_amount_minor__gt=0),
                name="tenancy_lease_rent_positive",
            ),
            models.CheckConstraint(
                condition=models.Q(deposit_amount_minor__gte=0),
                name="tenancy_lease_deposit_non_negative",
            ),
            models.CheckConstraint(
                condition=models.Q(service_charge_minor__gte=0),
                name="tenancy_lease_service_charge_non_negative",
            ),
            models.CheckConstraint(
                condition=models.Q(payment_due_day__gte=1, payment_due_day__lte=28),
                name="tenancy_lease_payment_due_day_range",
            ),
            models.CheckConstraint(
                condition=models.Q(grace_period_days__gte=0, grace_period_days__lte=30),
                name="tenancy_lease_grace_period_range",
            ),
            models.CheckConstraint(
                condition=models.Q(status__in=sorted(LeaseStatus.VALUES)),
                name="tenancy_lease_status_valid",
            ),
            ExclusionConstraint(
                name="tenancy_lease_exclude_overlapping_contractual_leases",
                expressions=[
                    ("property_id", RangeOperators.EQUAL),
                    (
                        DateRange(F("start_date"), F("effective_end_date"), Value("[)")),
                        RangeOperators.OVERLAPS,
                    ),
                ],
                condition=models.Q(status__in=sorted(LeaseStatus.OCCUPANCY_RESERVING)),
            ),
        ]
        indexes = [
            models.Index(fields=["property", "status"], name="tenancy_lease_prop_stat_idx"),
            models.Index(fields=["managing_organization", "status"], name="tenancy_lse_org_stat_idx"),
            models.Index(fields=["start_date", "effective_end_date"], name="tenancy_lease_dates_idx"),
        ]

    def clean(self):
        super().clean()
        if self.property_id:
            from apps.tenancy.services.validation import validate_rentable_unit
            validate_rentable_unit(self.property)

    def save(self, *args, **kwargs):
        if self._state.adding and self.property_id:
            from apps.tenancy.services.validation import validate_rentable_unit
            validate_rentable_unit(self.property)

        if not self._state.adding and self.pk:
            watched = (
                "status",
                "effective_end_date",
                "signature_deadline",
                *IMMUTABLE_CONTRACT_FIELDS,
            )
            original = Lease.objects.filter(pk=self.pk).values(*watched).first()
            if original:
                orig_status = original["status"]
                if not lease_transition_allowed(orig_status, self.status):
                    raise ValidationError(STATUS_INVALID)
                if orig_status in LeaseStatus.EXECUTED:
                    for field in IMMUTABLE_CONTRACT_FIELDS:
                        curr_val = getattr(self, field)
                        if original[field] != curr_val:
                            raise ValidationError(
                                f"Contractual field '{field}' is immutable on executed lease {self.reference}. "
                                f"Formal contractual variations must use LeaseAmendment."
                            )
                elif orig_status == LeaseStatus.PENDING_SIGNATURE:
                    frozen = IMMUTABLE_CONTRACT_FIELDS | PENDING_RESERVATION_FIELDS
                    for field in frozen:
                        if original[field] != getattr(self, field):
                            raise ValidationError(
                                f"Contractual field '{field}' is immutable once lease "
                                f"{self.reference} is approved."
                            )
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.status in LeaseStatus.EXECUTED:
            raise ValidationError(
                f"Executed lease {self.reference} cannot be deleted. Use formal termination."
            )
        return super().delete(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.reference} ({self.status}) - Property {self.property_id}"


class LeasePartyQuerySet(models.QuerySet):
    """QuerySet for LeaseParty preventing unauthorized mutations on executed leases."""

    def update(self, **kwargs):
        if self.filter(lease__status__in=LeaseStatus.EXECUTED).exists():
            raise ValidationError(
                "Direct update of parties on executed leases is prohibited."
            )
        return super().update(**kwargs)

    def delete(self):
        if self.filter(lease__status__in=LeaseStatus.EXECUTED).exists():
            raise ValidationError(
                "Parties on executed leases cannot be deleted."
            )
        return super().delete()


class LeaseParty(models.Model):
    """Membership, legal role, and digital signature status for a party to a lease."""

    objects = LeasePartyQuerySet.as_manager()

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    lease = models.ForeignKey(
        Lease,
        on_delete=models.CASCADE,
        related_name="parties",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="lease_parties",
    )
    party_role = models.CharField(max_length=32, choices=LeasePartyRole.CHOICES)
    has_signature_authority = models.BooleanField(
        default=True,
        help_text="Whether this party role has contractual signing authority.",
    )
    signature_status = models.CharField(
        max_length=32,
        choices=PartySignatureStatus.CHOICES,
        default=PartySignatureStatus.PENDING,
    )
    signed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "tenancy_lease_party"
        ordering = ["created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["lease", "user", "party_role"],
                name="tenancy_lease_party_unique_role",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        party_role=LeasePartyRole.AUTHORIZED_OCCUPANT,
                        has_signature_authority=False,
                    )
                    | ~models.Q(party_role=LeasePartyRole.AUTHORIZED_OCCUPANT)
                ),
                name="tenancy_party_occupant_no_sig_auth",
            ),
            models.CheckConstraint(
                condition=models.Q(party_role__in=sorted(LeasePartyRole.VALUES)),
                name="tenancy_party_role_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(signature_status__in=sorted(PartySignatureStatus.VALUES)),
                name="tenancy_party_signature_status_valid",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.party_role}: {self.user_id} on {self.lease.reference} ({self.signature_status})"

    def save(self, *args, **kwargs):
        if not self._state.adding and self.pk:
            if self.lease.status in LeaseStatus.EXECUTED:
                original = (
                    LeaseParty.objects.filter(pk=self.pk)
                    .values("user_id", "party_role", "has_signature_authority")
                    .first()
                )
                if original and (
                    original["user_id"] != self.user_id
                    or original["party_role"] != self.party_role
                    or original["has_signature_authority"] != self.has_signature_authority
                ):
                    raise ValidationError(
                        f"Cannot mutate signatory party on executed lease {self.lease.reference}."
                    )
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.lease.status in LeaseStatus.EXECUTED:
            raise ValidationError(
                f"Cannot delete party from executed lease {self.lease.reference}."
            )
        return super().delete(*args, **kwargs)


class LeaseSignatureEvidence(models.Model):
    """Immutable audit record and cryptographic evidence of digital signature execution."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    idempotency_key = models.CharField(max_length=64, unique=True)
    lease = models.ForeignKey(
        Lease,
        on_delete=models.PROTECT,
        related_name="signature_evidence",
    )
    party = models.ForeignKey(
        LeaseParty,
        on_delete=models.PROTECT,
        related_name="signatures",
    )
    signatory_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="lease_signatures_given",
    )
    signatory_role = models.CharField(max_length=32)
    document_version = models.PositiveIntegerField(default=1)
    contract_data_hash = models.CharField(
        max_length=64,
        help_text=CONTRACT_DATA_HASH_HELP,
    )
    rendered_document_hash = models.CharField(
        max_length=64,
        help_text=RENDERED_DOCUMENT_HASH_HELP,
    )
    rendered_document_uri = models.CharField(max_length=255, blank=True, default="")
    signature_method = models.CharField(max_length=32, choices=SignatureMethod.CHOICES)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True, default="")
    signed_at = models.DateTimeField(auto_now_add=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "tenancy_signature_evidence"
        ordering = ["-signed_at"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(signature_method__in=sorted(SignatureMethod.VALUES)),
                name="tenancy_sig_method_valid",
            ),
            models.CheckConstraint(
                condition=~models.Q(signatory_role=LeasePartyRole.AUTHORIZED_OCCUPANT),
                name="tenancy_sig_evidence_no_occupant",
            ),
            models.UniqueConstraint(
                fields=["lease", "party", "document_version"],
                name="tenancy_sig_evidence_unique_party_version",
            ),
        ]

    def clean(self):
        super().clean()
        if self.party_id:
            if self.party.party_role == LeasePartyRole.AUTHORIZED_OCCUPANT or not self.party.has_signature_authority:
                raise ValidationError(
                    f"Party role '{self.party.party_role}' lacks signature authority and cannot provide signature evidence."
                )
            if self.party.lease_id != self.lease_id:
                raise ValidationError("Signature party must belong to the specified lease.")
            if self.signatory_user_id != self.party.user_id:
                raise ValidationError("Signatory user must match the lease party user.")
            if self.signatory_role != self.party.party_role:
                raise ValidationError("Signatory role must match the lease party role.")

    def save(self, *args, **kwargs):
        if not self._state.adding and self.pk:
            raise ValidationError("LeaseSignatureEvidence records are append-only and immutable.")
        self.clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("LeaseSignatureEvidence records are immutable and cannot be deleted.")

    def __str__(self) -> str:
        return f"SigEvidence {self.id} for {self.signatory_role} on {self.lease_id}"


class LeaseAmendment(models.Model):
    """Formal bilateral contractual variation of terms for an executed lease."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    reference = models.CharField(
        max_length=32,
        unique=True,
        default=generate_amendment_reference,
        editable=False,
    )
    lease = models.ForeignKey(
        Lease,
        on_delete=models.PROTECT,
        related_name="amendments",
    )
    amendment_type = models.CharField(max_length=32, choices=AmendmentType.CHOICES)
    effective_date = models.DateField()
    old_rent_amount_minor = models.BigIntegerField()
    new_rent_amount_minor = models.BigIntegerField()
    old_effective_end_date = models.DateField(null=True, blank=True)
    new_effective_end_date = models.DateField(null=True, blank=True)
    reason = models.TextField()
    status = models.CharField(
        max_length=32,
        choices=AmendmentStatus.CHOICES,
        default=AmendmentStatus.DRAFT,
    )
    executed_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="created_amendments",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "tenancy_lease_amendment"
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(new_rent_amount_minor__gt=0),
                name="tenancy_amendment_new_rent_positive",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(new_effective_end_date__isnull=True)
                    | models.Q(new_effective_end_date__gt=models.F("effective_date"))
                ),
                name="tenancy_amendment_end_date_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(amendment_type__in=sorted(AmendmentType.VALUES)),
                name="tenancy_amendment_type_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(status__in=sorted(AmendmentStatus.VALUES)),
                name="tenancy_amendment_status_valid",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.reference} ({self.amendment_type}) - Lease {self.lease.reference}"


class RentScheduleEntry(models.Model):
    """Deterministic contractual rent demand schedule (Pure Obligation - No Cash Settlement)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    lease = models.ForeignKey(
        Lease,
        on_delete=models.PROTECT,
        related_name="rent_schedules",
    )
    sequence_number = models.PositiveSmallIntegerField()
    period_start = models.DateField()
    period_end = models.DateField()
    due_date = models.DateField()
    base_rent_minor = models.BigIntegerField()
    service_charge_minor = models.BigIntegerField(default=0)
    total_due_minor = models.BigIntegerField()
    currency = models.CharField(max_length=3, default="KES")
    status = models.CharField(
        max_length=16,
        choices=ScheduleStatus.CHOICES,
        default=ScheduleStatus.SCHEDULED,
    )
    billed_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancellation_reason = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "tenancy_rent_schedule_entry"
        ordering = ["lease", "sequence_number"]
        constraints = [
            models.UniqueConstraint(
                fields=["lease", "sequence_number"],
                name="tenancy_rent_schedule_seq_unique",
            ),
            models.CheckConstraint(
                condition=models.Q(period_end__gte=models.F("period_start")),
                name="tenancy_rent_schedule_period_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    total_due_minor=models.F("base_rent_minor") + models.F("service_charge_minor")
                ),
                name="tenancy_rent_schedule_total_matches",
            ),
            models.CheckConstraint(
                condition=models.Q(base_rent_minor__gt=0),
                name="tenancy_rent_schedule_base_rent_positive",
            ),
            models.CheckConstraint(
                condition=models.Q(service_charge_minor__gte=0),
                name="tenancy_rent_schedule_service_charge_non_neg",
            ),
            models.CheckConstraint(
                condition=models.Q(status__in=sorted(ScheduleStatus.VALUES)),
                name="tenancy_rent_schedule_status_valid",
            ),
        ]
        indexes = [
            models.Index(fields=["lease", "due_date", "status"], name="tenancy_sched_due_idx"),
        ]

    def __str__(self) -> str:
        return f"Schedule #{self.sequence_number} for {self.lease.reference} ({self.total_due_minor} {self.currency})"


class DepositObligation(models.Model):
    """Contractual security deposit liability established by a lease."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    lease = models.OneToOneField(
        Lease,
        on_delete=models.PROTECT,
        related_name="deposit_obligation",
    )
    tenant_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="deposit_obligations",
    )
    required_amount_minor = models.BigIntegerField()
    currency = models.CharField(max_length=3, default="KES")
    due_date = models.DateField()
    status = models.CharField(
        max_length=32,
        choices=DepositStatus.CHOICES,
        default=DepositStatus.HELD_PENDING,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "tenancy_deposit_obligation"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(required_amount_minor__gte=0),
                name="tenancy_deposit_required_non_negative",
            ),
            models.CheckConstraint(
                condition=models.Q(status__in=sorted(DepositStatus.VALUES)),
                name="tenancy_deposit_status_valid",
            ),
        ]

    def __str__(self) -> str:
        return f"Deposit {self.required_amount_minor} {self.currency} on {self.lease.reference}"


class DepositDeduction(models.Model):
    """Itemized contractual damage, cleaning, or holdover claim assessed against a deposit."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    deposit_obligation = models.ForeignKey(
        DepositObligation,
        on_delete=models.PROTECT,
        related_name="deductions",
    )
    handover_protocol = models.ForeignKey(
        "HandoverProtocol",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="deposit_deductions",
    )
    deduction_type = models.CharField(max_length=32, choices=DeductionType.CHOICES)
    description = models.TextField()
    amount_minor = models.BigIntegerField()
    dispute_status = models.CharField(
        max_length=32,
        choices=DeductionDisputeStatus.CHOICES,
        default=DeductionDisputeStatus.PROPOSED,
    )
    dispute_reason = models.TextField(blank=True, default="")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="created_deposit_deductions",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "tenancy_deposit_deduction"
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(amount_minor__gt=0),
                name="tenancy_deposit_deduction_amount_positive",
            ),
            models.CheckConstraint(
                condition=models.Q(deduction_type__in=sorted(DeductionType.VALUES)),
                name="tenancy_deduction_type_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(dispute_status__in=sorted(DeductionDisputeStatus.VALUES)),
                name="tenancy_deduction_dispute_status_valid",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.deduction_type}: {self.amount_minor} on {self.deposit_obligation_id}"


class HandoverProtocol(models.Model):
    """Bilateral condition inspection and key handover documentation at Move-In and Move-Out."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    lease = models.ForeignKey(
        Lease,
        on_delete=models.PROTECT,
        related_name="handover_protocols",
    )
    protocol_type = models.CharField(max_length=16, choices=HandoverType.CHOICES)
    handover_date = models.DateField()
    keys_handed_over_count = models.PositiveSmallIntegerField(default=1)
    physical_condition_summary = models.TextField()
    meter_readings_snapshot = models.JSONField(default=dict)
    signed_by_agent_at = models.DateTimeField(null=True, blank=True)
    signed_by_tenant_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(
        max_length=32,
        choices=HandoverStatus.CHOICES,
        default=HandoverStatus.DRAFT,
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="created_handovers",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "tenancy_handover_protocol"
        ordering = ["-handover_date"]
        constraints = [
            models.UniqueConstraint(
                fields=["lease", "protocol_type"],
                name="tenancy_handover_one_per_type",
            ),
            models.CheckConstraint(
                condition=models.Q(protocol_type__in=sorted(HandoverType.VALUES)),
                name="tenancy_handover_type_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(status__in=sorted(HandoverStatus.VALUES)),
                name="tenancy_handover_status_valid",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.protocol_type} ({self.status}) for Lease {self.lease.reference}"


class TenancyEvent(models.Model):
    """Tamper-evident, append-only operational audit log for contractual tenancy events."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    idempotency_key = models.CharField(max_length=64, unique=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="tenancy_events",
    )
    lease = models.ForeignKey(
        Lease,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="events",
    )
    application = models.ForeignKey(
        RentalApplication,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="events",
    )
    action = models.CharField(max_length=64, choices=TenancyEventAction.CHOICES)
    object_type = models.CharField(max_length=64)
    object_id = models.CharField(max_length=36)
    request_id = models.CharField(max_length=128, blank=True, default="")
    metadata = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = AppendOnlyQuerySet.as_manager()

    class Meta:
        db_table = "tenancy_event"
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(action__in=sorted(TenancyEventAction.VALUES)),
                name="tenancy_event_action_valid",
            ),
        ]
        indexes = [
            models.Index(fields=["lease", "created_at"], name="tenancy_event_lease_idx"),
            models.Index(fields=["application", "created_at"], name="tenancy_event_app_idx"),
        ]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValueError("Tenancy events are append-only and cannot be updated.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError("Tenancy events are append-only and cannot be deleted.")
