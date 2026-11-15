"""Inspect-only marketplace admin. It does not verify title or publish listings."""

from django.contrib import admin

from apps.marketplace.models import (
    Listing,
    ListingEvent,
    ListingMandate,
    ListingMedia,
    ListingPrice,
    MediaUploadSession,
    PropertyMedia,
)


class ReadOnlyAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Listing)
class ListingAdmin(ReadOnlyAdmin):
    list_display = ("reference", "listing_type", "status", "advertiser", "published_at")
    list_filter = ("status", "listing_type")
    search_fields = ("reference", "title")


@admin.register(ListingMandate)
class ListingMandateAdmin(ReadOnlyAdmin):
    list_display = ("property", "listing_scope", "status", "self_declared", "valid_until")
    list_filter = ("status", "self_declared", "listing_scope")


@admin.register(ListingPrice)
class ListingPriceAdmin(ReadOnlyAdmin):
    list_display = ("listing", "currency", "amount_minor", "billing_period", "superseded_at")


@admin.register(PropertyMedia)
class PropertyMediaAdmin(ReadOnlyAdmin):
    list_display = ("property", "kind", "mime_type", "upload_status", "visibility")
    list_filter = ("kind", "upload_status", "visibility")


@admin.register(MediaUploadSession)
class MediaUploadSessionAdmin(ReadOnlyAdmin):
    list_display = ("status", "kind", "storage_provider", "expires_at", "object_deleted")
    list_filter = ("status", "kind", "storage_provider")


@admin.register(ListingMedia)
class ListingMediaAdmin(ReadOnlyAdmin):
    list_display = ("listing", "sort_order", "featured")


@admin.register(ListingEvent)
class ListingEventAdmin(ReadOnlyAdmin):
    list_display = ("action", "property", "listing", "created_at")
    list_filter = ("action",)
