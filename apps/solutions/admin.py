from django.contrib import admin

from apps.solutions.models import (
    AuctionDocument,
    AuctionListing,
    CommercialEnquiry,
    CommercialEnquiryEvent,
    CommercialFollowUp,
    DevelopmentProject,
    InsuranceProduct,
    InvestmentOpportunity,
    ServiceCategory,
    ServiceProvider,
)


class AuctionDocumentInline(admin.TabularInline):
    model = AuctionDocument
    extra = 0


class EnquiryEventInline(admin.TabularInline):
    model = CommercialEnquiryEvent
    extra = 0
    can_delete = False
    readonly_fields = ("action", "actor", "request_id", "metadata", "created_at")

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False


class FollowUpInline(admin.TabularInline):
    model = CommercialFollowUp
    extra = 0
    readonly_fields = ("actor", "channel", "note", "created_at")

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(AuctionListing)
class AuctionAdmin(admin.ModelAdmin):
    list_display = ("reference", "title", "status", "county", "auctioneer_verified")
    list_filter = ("status", "category")
    search_fields = ("reference", "title", "county")
    inlines = [AuctionDocumentInline]


@admin.register(DevelopmentProject)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "status", "category", "location_label")
    list_filter = ("status", "category")
    search_fields = ("name", "slug")


@admin.register(InsuranceProduct)
class InsuranceAdmin(admin.ModelAdmin):
    list_display = ("name", "line", "category", "quotation_mode", "api_configured", "status")
    list_filter = ("line", "status", "quotation_mode")


@admin.register(InvestmentOpportunity)
class InvestmentAdmin(admin.ModelAdmin):
    list_display = ("reference", "name", "symbol", "category", "status")
    list_filter = ("category", "status")


@admin.register(ServiceCategory)
class ServiceCategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "group", "slug")
    list_filter = ("group",)


@admin.register(ServiceProvider)
class ServiceProviderAdmin(admin.ModelAdmin):
    list_display = ("name", "category", "status", "credentials_checked")
    list_filter = ("status", "credentials_checked")


@admin.register(CommercialEnquiry)
class EnquiryAdmin(admin.ModelAdmin):
    list_display = ("reference", "category", "status", "subject_reference", "created_at")
    list_filter = ("category", "status")
    search_fields = ("reference", "subject_reference")
    inlines = [FollowUpInline, EnquiryEventInline]
