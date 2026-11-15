"""Location reference routes."""

from django.urls import path

from apps.locations.views import (
    AddressResolveView,
    AdministrativeAreaHierarchyView,
    AdministrativeAreaListView,
    CountryListView,
    LocalitySearchView,
    LocationSearchView,
)

urlpatterns = [
    path("locations/countries/", CountryListView.as_view(), name="location-countries"),
    path(
        "locations/countries/<str:country>/areas/",
        AdministrativeAreaListView.as_view(),
        name="location-areas",
    ),
    path(
        "locations/areas/<uuid:area_id>/",
        AdministrativeAreaHierarchyView.as_view(),
        name="location-area-hierarchy",
    ),
    path("locations/localities/", LocalitySearchView.as_view(), name="location-localities"),
    path("locations/search/", LocationSearchView.as_view(), name="location-search"),
    path(
        "locations/addresses/resolve/",
        AddressResolveView.as_view(),
        name="location-address-resolve",
    ),
]
