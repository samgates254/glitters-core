from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.forms import UserChangeForm, UserCreationForm

from apps.identity.domain import AccountStatus
from apps.identity.models import IdentityEvent, User
from apps.identity.services.lifecycle import change_status


class EmailUserCreationForm(UserCreationForm):
    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("email",)


class EmailUserChangeForm(UserChangeForm):
    class Meta(UserChangeForm.Meta):
        model = User
        fields = "__all__"


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    form = EmailUserChangeForm
    add_form = EmailUserCreationForm
    ordering = ("email",)
    list_display = ("email", "first_name", "last_name", "status", "is_staff", "is_active")
    list_filter = ("status", "is_staff", "is_superuser", "is_active")
    search_fields = ("email", "first_name", "last_name", "phone_e164")
    readonly_fields = (
        "date_joined",
        "updated_at",
        "last_login",
        "is_active",
        "email_verified_at",
        "phone_verified_at",
    )
    filter_horizontal = ("groups", "user_permissions")
    actions = ("suspend_accounts", "restore_accounts")
    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("Personal", {"fields": ("first_name", "last_name", "phone_e164", "phone_verified_at")}),
        (
            "Account",
            {"fields": ("status", "email_verified_at", "is_active", "is_staff", "is_superuser")},
        ),
        ("Access", {"fields": ("groups", "user_permissions")}),
        ("Dates", {"fields": ("last_login", "date_joined", "updated_at")}),
    )
    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": ("email", "password1", "password2", "is_staff", "is_superuser"),
            },
        ),
    )

    @admin.action(description="Suspend selected accounts")
    def suspend_accounts(self, request, queryset):
        for user in queryset:
            if user.pk == request.user.pk:
                continue
            change_status(user, AccountStatus.SUSPENDED, force=True)

    @admin.action(description="Restore selected accounts to active")
    def restore_accounts(self, request, queryset):
        for user in queryset:
            change_status(user, AccountStatus.ACTIVE, force=True)


@admin.register(IdentityEvent)
class IdentityEventAdmin(admin.ModelAdmin):
    list_display = ("created_at", "event_type", "user", "ip_address")
    list_filter = ("event_type",)
    search_fields = ("event_type", "user__email")
    readonly_fields = ("user", "event_type", "ip_address", "user_agent", "created_at")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
