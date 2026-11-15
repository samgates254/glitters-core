"""Admin configuration for tenancy models with strict immutability locks."""

from django.contrib import admin

from apps.tenancy.domain import LeaseStatus
from apps.tenancy.models import (
    DepositDeduction,
    DepositObligation,
    HandoverProtocol,
    IMMUTABLE_CONTRACT_FIELDS,
    Lease,
    LeaseAmendment,
    LeaseParty,
    LeaseSignatureEvidence,
    RentalApplication,
    RentScheduleEntry,
    TenancyEvent,
)


class ReadOnlyAdmin(admin.ModelAdmin):
    """Admin class for append-only and immutable records."""

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(RentalApplication)
class RentalApplicationAdmin(admin.ModelAdmin):
    list_display = ("reference", "property", "applicant", "status", "proposed_start_date", "created_at")
    list_filter = ("status",)
    search_fields = ("reference", "property__name", "applicant__email")
    readonly_fields = ("reference", "created_at", "updated_at")


class LeasePartyInline(admin.TabularInline):
    model = LeaseParty
    extra = 0

    def has_delete_permission(self, request, obj=None):
        if obj and obj.status in LeaseStatus.EXECUTED:
            return False
        return super().has_delete_permission(request, obj)


@admin.register(Lease)
class LeaseAdmin(admin.ModelAdmin):
    list_display = (
        "reference",
        "property",
        "property_owner",
        "status",
        "start_date",
        "effective_end_date",
        "rent_amount_minor",
        "created_at",
    )
    list_filter = ("status", "lease_type")
    search_fields = ("reference", "property__name", "property_owner__email")
    readonly_fields = ("reference", "created_at", "updated_at")
    inlines = [LeasePartyInline]

    def get_readonly_fields(self, request, obj=None):
        readonly = list(super().get_readonly_fields(request, obj))
        if obj and obj.status in LeaseStatus.EXECUTED:
            readonly.extend(list(IMMUTABLE_CONTRACT_FIELDS))
        return readonly

    def has_delete_permission(self, request, obj=None):
        if obj and obj.status in LeaseStatus.EXECUTED:
            return False
        return super().has_delete_permission(request, obj)


@admin.register(LeaseParty)
class LeasePartyAdmin(admin.ModelAdmin):
    list_display = ("lease", "user", "party_role", "has_signature_authority", "signature_status", "signed_at")
    list_filter = ("party_role", "signature_status", "has_signature_authority")
    search_fields = ("lease__reference", "user__email")

    def has_delete_permission(self, request, obj=None):
        if obj and obj.lease.status in LeaseStatus.EXECUTED:
            return False
        return super().has_delete_permission(request, obj)


@admin.register(LeaseSignatureEvidence)
class LeaseSignatureEvidenceAdmin(ReadOnlyAdmin):
    list_display = ("id", "lease", "signatory_user", "signatory_role", "document_version", "signed_at")
    list_filter = ("signatory_role", "signature_method")
    search_fields = ("lease__reference", "signatory_user__email", "idempotency_key")


@admin.register(LeaseAmendment)
class LeaseAmendmentAdmin(admin.ModelAdmin):
    list_display = ("reference", "lease", "amendment_type", "status", "effective_date", "created_at")
    list_filter = ("status", "amendment_type")
    search_fields = ("reference", "lease__reference")
    readonly_fields = ("reference", "created_at", "updated_at")


@admin.register(RentScheduleEntry)
class RentScheduleEntryAdmin(admin.ModelAdmin):
    list_display = ("lease", "sequence_number", "due_date", "base_rent_minor", "status")
    list_filter = ("status",)
    search_fields = ("lease__reference",)


@admin.register(DepositObligation)
class DepositObligationAdmin(admin.ModelAdmin):
    list_display = ("lease", "required_amount_minor", "currency", "status")
    list_filter = ("status",)
    search_fields = ("lease__reference",)


@admin.register(DepositDeduction)
class DepositDeductionAdmin(admin.ModelAdmin):
    list_display = ("deposit_obligation", "deduction_type", "amount_minor", "dispute_status")
    list_filter = ("deduction_type", "dispute_status")
    search_fields = ("deposit_obligation__lease__reference",)


@admin.register(HandoverProtocol)
class HandoverProtocolAdmin(admin.ModelAdmin):
    list_display = ("id", "lease", "protocol_type", "status", "handover_date")
    list_filter = ("protocol_type", "status")
    search_fields = ("lease__reference",)
    readonly_fields = ("created_at", "updated_at")


@admin.register(TenancyEvent)
class TenancyEventAdmin(ReadOnlyAdmin):
    list_display = ("action", "object_type", "object_id", "actor", "lease", "created_at")
    list_filter = ("action", "object_type")
    search_fields = ("idempotency_key", "object_id", "lease__reference")
