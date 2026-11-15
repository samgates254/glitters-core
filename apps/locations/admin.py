"""Inspect-only location admin. Reference rows are seed data, not ad-hoc edits."""

from django.contrib import admin

from apps.locations.models import (
    Address,
    AdministrativeArea,
    AdministrativeLevel,
    Country,
    Locality,
)


class ReadOnlyAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Country)
class CountryAdmin(ReadOnlyAdmin):
    list_display = ("iso_alpha2", "name", "iso_alpha3", "active")
    search_fields = ("iso_alpha2", "name")


@admin.register(AdministrativeLevel)
class AdministrativeLevelAdmin(ReadOnlyAdmin):
    list_display = ("country", "depth", "code", "label")
    list_filter = ("country",)


@admin.register(AdministrativeArea)
class AdministrativeAreaAdmin(ReadOnlyAdmin):
    list_display = ("name", "country", "level", "parent", "code")
    search_fields = ("name", "code")
    list_filter = ("country",)


@admin.register(Locality)
class LocalityAdmin(ReadOnlyAdmin):
    list_display = ("name", "kind", "country", "administrative_area")
    search_fields = ("name",)
    list_filter = ("kind", "country")


@admin.register(Address)
class AddressAdmin(ReadOnlyAdmin):
    list_display = ("country", "administrative_area", "locality", "street_line")
    search_fields = ("street_line", "postal_code")
