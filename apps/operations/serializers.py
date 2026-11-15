"""Serializers for operations management agreements, delegated access, and physical profiles."""

from __future__ import annotations

from rest_framework import serializers

from apps.operations.domain import AgreementStatus, PhysicalState
from apps.operations.models import DelegatedPropertyAccess, ManagementAgreement, UnitPhysicalProfile


class ManagementAgreementSerializer(serializers.ModelSerializer):
    property_id = serializers.UUIDField(source="property.id", read_only=True)
    managing_organization_id = serializers.UUIDField(source="managing_organization.id", read_only=True)
    granting_user_id = serializers.UUIDField(source="granting_user.id", read_only=True)
    assigned_agent_id = serializers.UUIDField(source="assigned_agent.id", read_only=True, allow_null=True)
    created_by_id = serializers.UUIDField(source="created_by.id", read_only=True)
    lease_execution_revoked_by_id = serializers.UUIDField(source="lease_execution_revoked_by.id", read_only=True, allow_null=True)
    has_effective_lease_execution_authority = serializers.BooleanField(read_only=True)

    class Meta:
        model = ManagementAgreement
        fields = [
            "id",
            "reference",
            "property_id",
            "managing_organization_id",
            "granting_user_id",
            "assigned_agent_id",
            "spend_limit_minor",
            "commission_basis_points",
            "valid_from",
            "valid_until",
            "can_execute_leases",
            "lease_execution_revoked_at",
            "lease_execution_revoked_by_id",
            "lease_execution_revocation_reason",
            "has_effective_lease_execution_authority",
            "status",
            "accepted_by_owner_at",
            "accepted_by_agent_at",
            "activated_at",
            "terminated_at",
            "termination_reason",
            "created_by_id",
            "created_at",
            "updated_at",
        ]


class ManagementAgreementCreateSerializer(serializers.Serializer):
    property_id = serializers.UUIDField()
    managing_organization_id = serializers.UUIDField()
    granting_user_id = serializers.UUIDField(required=False, allow_null=True)
    assigned_agent_id = serializers.UUIDField(required=False, allow_null=True)
    spend_limit_minor = serializers.IntegerField(default=1_000_000, min_value=0)
    commission_basis_points = serializers.IntegerField(default=800, min_value=0, max_value=10000)
    valid_from = serializers.DateField(required=False)
    valid_until = serializers.DateField(required=False, allow_null=True)
    can_execute_leases = serializers.BooleanField(default=False)
    as_draft = serializers.BooleanField(default=False)


class AgreementActionReasonSerializer(serializers.Serializer):
    reason = serializers.CharField(required=False, allow_blank=True, max_length=500, default="")


class DelegatedPropertyAccessSerializer(serializers.ModelSerializer):
    agreement_reference = serializers.CharField(source="agreement.reference", read_only=True)
    property_id = serializers.UUIDField(source="agreement.property_id", read_only=True)

    class Meta:
        model = DelegatedPropertyAccess
        fields = [
            "id",
            "agreement_reference",
            "property_id",
            "user",
            "organization",
            "status",
            "expires_at",
            "revoked_at",
            "created_at",
        ]


class UnitPhysicalProfileSerializer(serializers.ModelSerializer):
    property_id = serializers.UUIDField(source="property.id", read_only=True)
    updated_by_id = serializers.UUIDField(source="updated_by.id", read_only=True, allow_null=True)

    class Meta:
        model = UnitPhysicalProfile
        fields = [
            "id",
            "property_id",
            "physical_state",
            "target_rent_minor",
            "target_currency",
            "ready_at",
            "notes",
            "updated_by_id",
            "updated_at",
        ]


class UnitPhysicalProfileUpdateSerializer(serializers.Serializer):
    physical_state = serializers.ChoiceField(choices=PhysicalState.CHOICES, required=False)
    target_rent_minor = serializers.IntegerField(min_value=0, required=False, allow_null=True)
    target_currency = serializers.CharField(max_length=3, required=False)
    notes = serializers.CharField(max_length=1000, required=False, allow_blank=True)


class ManagedPortfolioPropertySerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField()
    reference = serializers.CharField()
    property_type = serializers.CharField()
    property_subtype = serializers.CharField()
    management_context = serializers.CharField()
    status = serializers.CharField()
    physical_state = serializers.CharField()
    ready_at = serializers.DateTimeField(allow_null=True)
    target_rent_minor = serializers.IntegerField(allow_null=True)
    target_currency = serializers.CharField()
    active_agreement_reference = serializers.CharField(allow_null=True)
