"""Inspect-only organization admin. Mutations go through the audited API."""

from django.contrib import admin

from apps.organizations.models import (
    Organization,
    OrganizationEvent,
    OrganizationInvitation,
    OrganizationMembership,
)


class ReadOnlyAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Organization)
class OrganizationAdmin(ReadOnlyAdmin):
    list_display = ("display_name", "slug", "organization_type", "status", "country", "created_at")
    list_filter = ("status", "organization_type", "country")
    search_fields = ("display_name", "legal_name", "slug", "registration_reference")
    readonly_fields = (
        "id",
        "legal_name",
        "display_name",
        "slug",
        "organization_type",
        "registration_reference",
        "contact_email",
        "contact_phone",
        "website",
        "country",
        "region",
        "address_line",
        "status",
        "created_at",
        "updated_at",
    )


@admin.register(OrganizationMembership)
class OrganizationMembershipAdmin(ReadOnlyAdmin):
    list_display = ("organization", "user", "role", "status", "source", "joined_at")
    list_filter = ("role", "status", "source")
    search_fields = ("user__email", "organization__slug", "organization__display_name")


@admin.register(OrganizationInvitation)
class OrganizationInvitationAdmin(ReadOnlyAdmin):
    list_display = ("email", "organization", "role", "status", "expires_at")
    list_filter = ("status", "role")
    search_fields = ("email", "organization__slug")
    exclude = ("token_hash",)


@admin.register(OrganizationEvent)
class OrganizationEventAdmin(ReadOnlyAdmin):
    list_display = ("created_at", "action", "organization", "actor", "object_type")
    list_filter = ("action",)
    search_fields = ("action", "object_id", "actor__email", "organization__slug")
    readonly_fields = (
        "actor",
        "organization",
        "action",
        "object_type",
        "object_id",
        "request_id",
        "ip_address",
        "metadata",
        "created_at",
    )
