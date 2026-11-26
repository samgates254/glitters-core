from django.urls import include, path

urlpatterns = [path("api/v1/construction/", include("apps.construction.urls"))]
