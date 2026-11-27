from rest_framework import serializers

from apps.backoffice.domain import AccessScope, StaffRole
from apps.backoffice.models import StaffRoleEvent, StaffRoleGrant


class StaffRoleGrantCreateSerializer(serializers.Serializer):
    user_id = serializers.UUIDField()
    role = serializers.ChoiceField(choices=StaffRole.CHOICES)
    scope = serializers.ChoiceField(choices=AccessScope.CHOICES)
    organization_id = serializers.UUIDField(required=False, allow_null=True)
    expires_at = serializers.DateTimeField(required=False, allow_null=True)

    def validate(self, attrs):
        scope = attrs["scope"]
        organization_id = attrs.get("organization_id")
        if scope == AccessScope.ORGANIZATION and organization_id is None:
            raise serializers.ValidationError(
                {"organization_id": "An organization-scoped role requires an organization."}
            )
        if scope == AccessScope.PLATFORM and organization_id is not None:
            raise serializers.ValidationError(
                {"organization_id": "A platform-scoped role cannot target an organization."}
            )
        return attrs


class StaffRoleGrantSerializer(serializers.ModelSerializer):
    user_id = serializers.UUIDField(read_only=True)
    organization_id = serializers.UUIDField(read_only=True, allow_null=True)
    granted_by_id = serializers.UUIDField(read_only=True)
    revoked_by_id = serializers.UUIDField(read_only=True, allow_null=True)
    is_active = serializers.BooleanField(read_only=True)

    class Meta:
        model = StaffRoleGrant
        fields = (
            "id",
            "user_id",
            "role",
            "scope",
            "organization_id",
            "granted_by_id",
            "created_at",
            "expires_at",
            "revoked_at",
            "revoked_by_id",
            "is_active",
        )
        read_only_fields = fields


class StaffRoleEventSerializer(serializers.ModelSerializer):
    grant_id = serializers.UUIDField(read_only=True)
    actor_id = serializers.UUIDField(read_only=True)

    class Meta:
        model = StaffRoleEvent
        fields = ("id", "grant_id", "action", "actor_id", "created_at", "metadata")
        read_only_fields = fields


class DashboardMetricSerializer(serializers.Serializer):
    id = serializers.CharField()
    label = serializers.CharField()
    value = serializers.JSONField()
    source = serializers.ListField(child=serializers.CharField())
    scope = serializers.JSONField()
    as_of = serializers.DateTimeField(allow_null=True)


class DashboardOverviewSerializer(serializers.Serializer):
    generated_at = serializers.DateTimeField()
    scope = serializers.JSONField()
    widgets = DashboardMetricSerializer(many=True)
