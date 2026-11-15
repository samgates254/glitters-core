"""Operational HTTP endpoints."""

from django.conf import settings
from drf_spectacular.utils import extend_schema
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView
from rest_framework.authentication import SessionAuthentication
from rest_framework.permissions import AllowAny, IsAdminUser
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.serializers import HealthLiveSerializer, HealthReadySerializer
from apps.core.services.health import live_payload, ready_payload


class HealthLiveView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = []

    @extend_schema(responses={200: HealthLiveSerializer}, auth=[])
    def get(self, request):
        return Response(live_payload(request.request_id))


class HealthReadyView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = []

    @extend_schema(responses={200: HealthReadySerializer, 503: HealthReadySerializer}, auth=[])
    def get(self, request):
        payload, status_code = ready_payload(request.request_id)
        return Response(payload, status=status_code)


class DocsAccessMixin:
    """Publish the schema in development. Require a staff session everywhere else."""

    authentication_classes = [SessionAuthentication]

    def get_permissions(self):
        if settings.DEBUG:
            return [AllowAny()]
        return [IsAdminUser()]


class SchemaView(DocsAccessMixin, SpectacularAPIView):
    pass


class DocsView(DocsAccessMixin, SpectacularSwaggerView):
    pass
