"""Inspect-only property admin. The coordinate column is not shown."""

from django.contrib import admin

from apps.properties.models import (
    Property,
    PropertyAccess,
    PropertyAttribute,
    PropertyEvent,
    PropertyLocation,
    PropertyParty,
)


class ReadOnlyAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Property)
class PropertyAdmin(ReadOnlyAdmin):
    list_display = (
        "reference",
        "name",
        "property_type",
        "status",
        "management_context",
        "managing_organization",
        "created_at",
    )
    list_filter = ("status", "property_type", "management_context", "hierarchy_role")
    search_fields = ("reference", "name")


@admin.register(PropertyLocation)
class PropertyLocationAdmin(ReadOnlyAdmin):
    list_display = ("property", "address", "coordinates_set_at")
    exclude = ("coordinates",)

    def get_fields(self, request, obj=None):
        fields = super().get_fields(request, obj)
        return [field for field in fields if field != "coordinates"]


@admin.register(PropertyAttribute)
class PropertyAttributeAdmin(ReadOnlyAdmin):
    list_display = ("property", "code", "unit")
    search_fields = ("code", "property__reference")


@admin.register(PropertyParty)
class PropertyPartyAdmin(ReadOnlyAdmin):
    list_display = ("property", "role", "status", "subject_kind", "created_at")
    list_filter = ("role", "status", "subject_kind")


@admin.register(PropertyAccess)
class PropertyAccessAdmin(ReadOnlyAdmin):
    list_display = ("property", "user", "capability", "status", "created_at")
    list_filter = ("capability", "status")


@admin.register(PropertyEvent)
class PropertyEventAdmin(ReadOnlyAdmin):
    list_display = ("created_at", "action", "property", "actor", "object_type")
    list_filter = ("action",)
    readonly_fields = (
        "actor",
        "property",
        "organization",
        "action",
        "object_type",
        "object_id",
        "request_id",
        "ip_address",
        "metadata",
        "created_at",
    )
