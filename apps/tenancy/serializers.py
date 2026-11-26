"""Request and response shapes for the tenancy API.

Serializers validate transport types. Lease rules stay in the services.
Signature IP addresses and user agents are not returned.
"""

from __future__ import annotations

import base64
import binascii

from rest_framework import serializers

from apps.tenancy.domain import (
    PERIODIC_LEASE_TYPES,
    LeasePartyRole,
    LeaseStatus,
    LeaseType,
    SignatureMethod,
)
from apps.tenancy.models import Lease, LeaseParty, LeaseSignatureEvidence, TenancyEvent
from apps.tenancy.services.leases import MAX_RENDERED_BYTES

_MAX_DOCUMENT_TEXT = ((MAX_RENDERED_BYTES + 2) // 3) * 4


def _validate_term_end(attrs: dict) -> dict:
    term_end = attrs.get("term_end_date")
    periodic = attrs.get("lease_type") in PERIODIC_LEASE_TYPES
    if term_end is None and not periodic:
        raise serializers.ValidationError({"term_end_date": "A fixed lease needs a term end date."})
    start = attrs.get("start_date")
    if term_end is not None and start is not None and term_end < start:
        raise serializers.ValidationError(
            {"term_end_date": "The term end date cannot be before the start date."}
        )
    return attrs


def decode_rendered_document(value: str) -> bytes:
    if not isinstance(value, str) or not value.strip():
        raise serializers.ValidationError("The rendered lease document is required.")
    if len(value) > _MAX_DOCUMENT_TEXT:
        raise serializers.ValidationError("The rendered lease document is too large.")
    try:
        document = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise serializers.ValidationError(
            "The rendered lease document is not valid base64."
        ) from exc
    if not document:
        raise serializers.ValidationError("The rendered lease document is required.")
    if len(document) > MAX_RENDERED_BYTES:
        raise serializers.ValidationError("The rendered lease document is too large.")
    return document


class PartyInputSerializer(serializers.Serializer):
    user_id = serializers.UUIDField()
    party_role = serializers.ChoiceField(choices=LeasePartyRole.CHOICES)
    has_signature_authority = serializers.BooleanField()


class DraftLeaseSerializer(serializers.Serializer):
    property_id = serializers.UUIDField()
    organization_id = serializers.UUIDField(required=False, allow_null=True)
    idempotency_key = serializers.CharField(required=False, allow_blank=True, max_length=200)
    lease_type = serializers.ChoiceField(choices=LeaseType.CHOICES)
    start_date = serializers.DateField()
    term_end_date = serializers.DateField(required=False, allow_null=True)
    rent_amount_minor = serializers.IntegerField(min_value=1)
    rent_currency = serializers.CharField(required=False, min_length=3, max_length=3)
    deposit_amount_minor = serializers.IntegerField(required=False, min_value=0)
    service_charge_minor = serializers.IntegerField(required=False, min_value=0)
    payment_due_day = serializers.IntegerField(required=False, min_value=1, max_value=28)
    grace_period_days = serializers.IntegerField(required=False, min_value=0, max_value=30)
    special_covenants = serializers.CharField(required=False, allow_blank=True, max_length=20_000)
    parties = PartyInputSerializer(many=True)
    application_id = serializers.UUIDField(required=False, allow_null=True)

    def validate(self, attrs):
        attrs = super().validate(attrs)
        return _validate_term_end(attrs)

    def validate_idempotency_key(self, value: str) -> str:
        return value.strip()

    def payload(self) -> dict:
        data = self.validated_data
        body = {
            "lease_type": data["lease_type"],
            "start_date": data["start_date"],
            "term_end_date": data.get("term_end_date"),
            "rent_amount_minor": data["rent_amount_minor"],
            "parties": [
                {
                    "user_id": party["user_id"],
                    "party_role": party["party_role"],
                    "has_signature_authority": party["has_signature_authority"],
                }
                for party in data["parties"]
            ],
        }
        for name in (
            "rent_currency",
            "deposit_amount_minor",
            "service_charge_minor",
            "payment_due_day",
            "grace_period_days",
            "special_covenants",
            "application_id",
        ):
            if name in data and data[name] is not None:
                body[name] = data[name]
        return body


class RenderedDocumentSerializer(serializers.Serializer):
    rendered_document_base64 = serializers.CharField()
    rendered_document_uri = serializers.CharField(required=False, allow_blank=True, max_length=255)
    deadline_hours = serializers.IntegerField(required=False, min_value=1, max_value=336)

    def document(self) -> bytes:
        return decode_rendered_document(self.validated_data["rendered_document_base64"])


class SignLeaseSerializer(serializers.Serializer):
    party_id = serializers.UUIDField()
    rendered_document_base64 = serializers.CharField()
    signature_method = serializers.ChoiceField(choices=SignatureMethod.CHOICES)
    idempotency_key = serializers.CharField(required=False, allow_blank=True, max_length=200)

    def validate_idempotency_key(self, value: str) -> str:
        return value.strip()

    def document(self) -> bytes:
        return decode_rendered_document(self.validated_data["rendered_document_base64"])


class RenewalLeaseSerializer(serializers.Serializer):
    organization_id = serializers.UUIDField(required=False, allow_null=True)
    idempotency_key = serializers.CharField(required=False, allow_blank=True, max_length=200)
    lease_type = serializers.ChoiceField(choices=LeaseType.CHOICES)
    start_date = serializers.DateField()
    term_end_date = serializers.DateField(required=False, allow_null=True)
    rent_amount_minor = serializers.IntegerField(min_value=1)
    rent_currency = serializers.CharField(required=False, min_length=3, max_length=3)
    deposit_amount_minor = serializers.IntegerField(required=False, min_value=0)
    service_charge_minor = serializers.IntegerField(required=False, min_value=0)
    payment_due_day = serializers.IntegerField(required=False, min_value=1, max_value=28)
    grace_period_days = serializers.IntegerField(required=False, min_value=0, max_value=30)
    special_covenants = serializers.CharField(required=False, allow_blank=True, max_length=20_000)
    parties = PartyInputSerializer(many=True)

    def validate(self, attrs):
        attrs = super().validate(attrs)
        return _validate_term_end(attrs)

    def validate_idempotency_key(self, value: str) -> str:
        return value.strip()

    def payload(self) -> dict:
        data = self.validated_data
        body = {
            "lease_type": data["lease_type"],
            "start_date": data["start_date"],
            "term_end_date": data.get("term_end_date"),
            "rent_amount_minor": data["rent_amount_minor"],
            "parties": [
                {
                    "user_id": party["user_id"],
                    "party_role": party["party_role"],
                    "has_signature_authority": party["has_signature_authority"],
                }
                for party in data["parties"]
            ],
        }
        for name in (
            "rent_currency",
            "deposit_amount_minor",
            "service_charge_minor",
            "payment_due_day",
            "grace_period_days",
            "special_covenants",
        ):
            if name in data and data[name] is not None:
                body[name] = data[name]
        return body


class CancellationSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=2000, trim_whitespace=True)
    idempotency_key = serializers.CharField(required=False, allow_blank=True, max_length=200)

    def validate_reason(self, value: str) -> str:
        if not value:
            raise serializers.ValidationError("A cancellation reason is required.")
        return value

    def validate_idempotency_key(self, value: str) -> str:
        return value.strip()


class AbandonRenewalSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=2000, trim_whitespace=True)
    organization_id = serializers.UUIDField(required=False, allow_null=True)
    idempotency_key = serializers.CharField(required=False, allow_blank=True, max_length=200)

    def validate_reason(self, value: str) -> str:
        if not value:
            raise serializers.ValidationError("A reason is required.")
        return value

    def validate_idempotency_key(self, value: str) -> str:
        return value.strip()


class TerminationSerializer(serializers.Serializer):
    termination_date = serializers.DateField()
    reason = serializers.CharField(max_length=2000, trim_whitespace=True)

    def validate_reason(self, value: str) -> str:
        if not value:
            raise serializers.ValidationError("A termination reason is required.")
        return value


class LeasePartySerializer(serializers.ModelSerializer):
    class Meta:
        model = LeaseParty
        fields = (
            "id",
            "user_id",
            "party_role",
            "has_signature_authority",
            "signature_status",
            "signed_at",
        )
        read_only_fields = fields


class LeaseListSerializer(serializers.ModelSerializer):
    occupancy_reserving = serializers.SerializerMethodField()

    class Meta:
        model = Lease
        fields = (
            "id",
            "reference",
            "status",
            "property_id",
            "managing_organization_id",
            "lease_type",
            "start_date",
            "term_end_date",
            "effective_end_date",
            "rent_amount_minor",
            "rent_currency",
            "signature_deadline",
            "occupancy_reserving",
        )
        read_only_fields = fields

    def get_occupancy_reserving(self, lease: Lease) -> bool:
        return lease.status in LeaseStatus.OCCUPANCY_RESERVING


class LeaseDetailSerializer(LeaseListSerializer):
    class Meta(LeaseListSerializer.Meta):
        fields = (
            *LeaseListSerializer.Meta.fields,
            "property_owner_id",
            "management_agreement_id",
            "deposit_amount_minor",
            "service_charge_minor",
            "payment_due_day",
            "grace_period_days",
            "special_covenants",
            "document_version",
            "contract_data_hash",
            "rendered_document_hash",
            "supersedes_lease_id",
            "actual_termination_date",
            "signed_at",
            "activated_at",
            "terminated_at",
            "termination_reason",
            "created_at",
        )
        read_only_fields = fields


class SignatureEvidenceSerializer(serializers.ModelSerializer):
    class Meta:
        model = LeaseSignatureEvidence
        fields = (
            "id",
            "party_id",
            "signatory_user_id",
            "signatory_role",
            "document_version",
            "contract_data_hash",
            "rendered_document_hash",
            "signature_method",
            "signed_at",
        )
        read_only_fields = fields


class TenancyEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = TenancyEvent
        fields = (
            "id",
            "action",
            "object_type",
            "object_id",
            "actor_id",
            "metadata",
            "created_at",
        )
        read_only_fields = fields
