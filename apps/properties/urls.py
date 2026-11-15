"""Property routes."""

from django.urls import path

from apps.properties.views import (
    PropertyAccessRevokeView,
    PropertyArchiveView,
    PropertyAuditView,
    PropertyCharacteristicView,
    PropertyCollectionView,
    PropertyDetailView,
    PropertyRelationshipDetailView,
    PropertyRelationshipRevokeView,
    PropertyRelationshipView,
)

urlpatterns = [
    path("properties/", PropertyCollectionView.as_view(), name="property-list"),
    path("properties/<uuid:property_id>/", PropertyDetailView.as_view(), name="property-detail"),
    path(
        "properties/<uuid:property_id>/archive/",
        PropertyArchiveView.as_view(),
        name="property-archive",
    ),
    path(
        "properties/<uuid:property_id>/characteristics/",
        PropertyCharacteristicView.as_view(),
        name="property-characteristics",
    ),
    path(
        "properties/<uuid:property_id>/relationships/",
        PropertyRelationshipView.as_view(),
        name="property-relationships",
    ),
    path(
        "properties/<uuid:property_id>/relationships/<uuid:party_id>/",
        PropertyRelationshipDetailView.as_view(),
        name="property-relationship-detail",
    ),
    path(
        "properties/<uuid:property_id>/relationships/<uuid:party_id>/revoke/",
        PropertyRelationshipRevokeView.as_view(),
        name="property-relationship-revoke",
    ),
    path(
        "properties/<uuid:property_id>/access/<uuid:access_id>/revoke/",
        PropertyAccessRevokeView.as_view(),
        name="property-access-revoke",
    ),
    path(
        "properties/<uuid:property_id>/audit/",
        PropertyAuditView.as_view(),
        name="property-audit",
    ),
]
