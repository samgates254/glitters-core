from django.urls import path

from apps.core.views import DocsView, HealthLiveView, HealthReadyView, SchemaView

urlpatterns = [
    path("health/live/", HealthLiveView.as_view(), name="health-live"),
    path("health/ready/", HealthReadyView.as_view(), name="health-ready"),
    path("schema/", SchemaView.as_view(), name="api-schema"),
    path("docs/", DocsView.as_view(url_name="api-schema"), name="api-docs"),
]
