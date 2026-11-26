"""Root URL configuration."""

from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/v1/", include("apps.core.urls")),
    path("api/v1/auth/", include("apps.identity.urls")),
    path("api/v1/", include("apps.organizations.urls")),
    path("api/v1/", include("apps.locations.urls")),
    path("api/v1/", include("apps.properties.urls")),
    path("api/v1/", include("apps.marketplace.urls")),
    path("api/v1/", include("apps.solutions.urls")),
    path("api/v1/operations/", include("apps.operations.urls")),
    path("api/v1/spatial/", include("apps.spatial.urls")),
    path("api/v1/tenancy/", include("apps.tenancy.urls")),
]
