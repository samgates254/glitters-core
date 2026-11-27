from django.contrib import admin

from apps.backoffice.models import StaffRoleEvent, StaffRoleGrant


class ReadOnlyAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(StaffRoleGrant)
class StaffRoleGrantAdmin(ReadOnlyAdmin):
    list_display = (
        "user",
        "role",
        "scope",
        "organization",
        "granted_by",
        "created_at",
        "revoked_at",
    )
    list_filter = ("role", "scope", "revoked_at")
    search_fields = ("user__email", "organization__slug")


@admin.register(StaffRoleEvent)
class StaffRoleEventAdmin(ReadOnlyAdmin):
    list_display = ("grant", "action", "actor", "created_at")
    list_filter = ("action",)
    search_fields = ("grant__user__email", "actor__email")
    readonly_fields = ("grant", "action", "actor", "created_at", "metadata")
