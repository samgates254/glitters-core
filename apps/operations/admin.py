"""Admin configuration for operations."""

from django.contrib import admin

from apps.operations.models import (
    DelegatedPropertyAccess,
    ManagementAgreement,
    OperationsEvent,
    UnitPhysicalProfile,
)


class ReadOnlyAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ManagementAgreement)
class ManagementAgreementAdmin(admin.ModelAdmin):
    list_display = (
        "reference",
        "property",
        "managing_organization",
        "granting_user",
        "status",
        "valid_from",
        "valid_until",
        "created_at",
    )
    list_filter = ("status", "valid_from")
    search_fields = ("reference", "property__name", "managing_organization__name")
    readonly_fields = (
        "reference",
        "created_at",
        "updated_at",
        "lease_execution_revoked_at",
        "lease_execution_revoked_by",
        "lease_execution_revocation_reason",
    )

    def get_readonly_fields(self, request, obj=None):
        readonly = list(super().get_readonly_fields(request, obj))
        if obj and obj.status in {
            "accepted",
            "active",
            "terminated",
            "revoked",
            "expired",
        }:
            readonly.extend([
                "can_execute_leases",
                "property",
                "managing_organization",
                "granting_user",
                "valid_from",
                "spend_limit_minor",
                "commission_basis_points",
            ])
        return readonly


@admin.register(DelegatedPropertyAccess)
class DelegatedPropertyAccessAdmin(admin.ModelAdmin):
    list_display = ("agreement", "user", "organization", "status", "created_at")
    list_filter = ("status",)
    search_fields = ("agreement__reference", "user__email", "organization__name")


@admin.register(UnitPhysicalProfile)
class UnitPhysicalProfileAdmin(admin.ModelAdmin):
    list_display = ("property", "physical_state", "target_rent_minor", "target_currency", "ready_at", "updated_at")
    list_filter = ("physical_state",)
    search_fields = ("property__name",)


@admin.register(OperationsEvent)
class OperationsEventAdmin(ReadOnlyAdmin):
    list_display = ("action", "object_type", "object_id", "actor", "property", "created_at")
    list_filter = ("action", "object_type")
    search_fields = ("idempotency_key", "object_id", "property__name")
