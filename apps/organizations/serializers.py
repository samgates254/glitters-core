"""Request and response shapes for organization resources."""

from __future__ import annotations

from rest_framework import serializers

from apps.organizations.domain import OrganizationStatus, OrganizationType, Role
from apps.organizations.models import (
    Organization,
    OrganizationEvent,
    OrganizationInvitation,
    OrganizationMembership,
)

ASSIGNABLE_ROLES = tuple((role, label) for role, label in Role.CHOICES if role != Role.OWNER)


class OrganizationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Organization
        fields = (
            "id",
            "legal_name",
            "display_name",
            "slug",
            "organization_type",
            "registration_reference",
            "contact_email",
            "contact_phone",
            "website",
            "country",
            "region",
            "address_line",
            "status",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields


class OrganizationCreateSerializer(serializers.Serializer):
    legal_name = serializers.CharField(max_length=200)
    display_name = serializers.CharField(max_length=200)
    slug = serializers.CharField(max_length=80, required=False, allow_blank=True)
    organization_type = serializers.ChoiceField(choices=OrganizationType.CHOICES)
    registration_reference = serializers.CharField(max_length=64, required=False, allow_blank=True)
    contact_email = serializers.EmailField(required=False, allow_blank=True)
    contact_phone = serializers.CharField(max_length=32, required=False, allow_blank=True)
    website = serializers.CharField(max_length=200, required=False, allow_blank=True)
    country = serializers.CharField(max_length=2, required=False, allow_blank=True)
    region = serializers.CharField(max_length=80, required=False, allow_blank=True)
    address_line = serializers.CharField(max_length=255, required=False, allow_blank=True)


class OrganizationUpdateSerializer(OrganizationCreateSerializer):
    legal_name = serializers.CharField(max_length=200, required=False)
    display_name = serializers.CharField(max_length=200, required=False)
    organization_type = serializers.ChoiceField(choices=OrganizationType.CHOICES, required=False)


class OrganizationStatusSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=OrganizationStatus.CHOICES)


class OrganizationStatusResultSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    status = serializers.CharField()


class MembershipSerializer(serializers.ModelSerializer):
    email = serializers.EmailField(source="user.email", read_only=True)
    organization_id = serializers.UUIDField(read_only=True)
    user_id = serializers.UUIDField(read_only=True)

    class Meta:
        model = OrganizationMembership
        fields = (
            "id",
            "organization_id",
            "user_id",
            "email",
            "role",
            "status",
            "source",
            "joined_at",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields


class OrganizationSummarySerializer(serializers.ModelSerializer):
    class Meta:
        model = Organization
        fields = ("id", "display_name", "slug", "organization_type", "status")
        read_only_fields = fields


class MyMembershipSerializer(serializers.ModelSerializer):
    organization = OrganizationSummarySerializer(read_only=True)

    class Meta:
        model = OrganizationMembership
        fields = (
            "id",
            "role",
            "status",
            "source",
            "joined_at",
            "created_at",
            "organization",
        )
        read_only_fields = fields


class MembershipRoleSerializer(serializers.Serializer):
    role = serializers.ChoiceField(choices=ASSIGNABLE_ROLES)


class InvitationSerializer(serializers.ModelSerializer):
    organization_id = serializers.UUIDField(read_only=True)

    class Meta:
        model = OrganizationInvitation
        fields = (
            "id",
            "organization_id",
            "email",
            "role",
            "status",
            "expires_at",
            "accepted_at",
            "revoked_at",
            "created_at",
        )
        read_only_fields = fields


class InvitationCreateSerializer(serializers.Serializer):
    email = serializers.EmailField()
    role = serializers.ChoiceField(choices=ASSIGNABLE_ROLES)


class InvitationTokenSerializer(serializers.Serializer):
    token = serializers.CharField(max_length=256)


class InvitationInspectSerializer(serializers.Serializer):
    organization_display_name = serializers.CharField()
    role = serializers.CharField()
    status = serializers.CharField()
    expires_at = serializers.DateTimeField()
    email_matches = serializers.BooleanField()


class OwnershipTransferSerializer(serializers.Serializer):
    membership_id = serializers.UUIDField()


class PermissionSerializer(serializers.Serializer):
    organization_id = serializers.UUIDField()
    role = serializers.CharField()
    permissions = serializers.ListField(child=serializers.CharField())


class RoleCatalogueSerializer(serializers.Serializer):
    roles = serializers.ListField()
    reserved_permissions = serializers.ListField(child=serializers.CharField())


class OrganizationEventSerializer(serializers.ModelSerializer):
    actor_id = serializers.UUIDField(allow_null=True, read_only=True)

    class Meta:
        model = OrganizationEvent
        fields = (
            "id",
            "actor_id",
            "action",
            "object_type",
            "object_id",
            "request_id",
            "ip_address",
            "metadata",
            "created_at",
        )
        read_only_fields = fields


class MessageSerializer(serializers.Serializer):
    detail = serializers.CharField()
