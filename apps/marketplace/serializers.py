"""Request and response shapes. Serializers do not decide publication."""

from __future__ import annotations

from rest_framework import serializers

from apps.marketplace.domain import AMOUNT_INVALID, PRICE_INVALID, EnquiryStatus


class PublicPropertySerializer(serializers.Serializer):
    property_type = serializers.CharField()
    property_subtype = serializers.CharField()
    bedrooms = serializers.IntegerField(allow_null=True)
    bathrooms = serializers.IntegerField(allow_null=True)
    floor_area_sqm = serializers.CharField(allow_null=True)
    land_area_sqm = serializers.CharField(allow_null=True)


class PublicLocationSerializer(serializers.Serializer):
    country = serializers.CharField(allow_null=True)
    administrative_area = serializers.CharField(allow_null=True)
    locality = serializers.CharField(allow_null=True)


class PublicPriceSerializer(serializers.Serializer):
    amount_minor = serializers.IntegerField(allow_null=True)
    currency = serializers.CharField()
    presentation = serializers.CharField()
    negotiable = serializers.BooleanField()
    billing_period = serializers.CharField()


class PublicAdvertiserSerializer(serializers.Serializer):
    display_name = serializers.CharField()
    organization_name = serializers.CharField(allow_null=True)


class PublicMediaSerializer(serializers.Serializer):
    mime_type = serializers.CharField()
    asset_reference = serializers.CharField()
    caption = serializers.CharField(allow_blank=True)
    alt_text = serializers.CharField(allow_blank=True)
    sort_order = serializers.IntegerField()
    featured = serializers.BooleanField()


class PublicListingSerializer(serializers.Serializer):
    reference = serializers.CharField()
    listing_type = serializers.CharField()
    title = serializers.CharField()
    description = serializers.CharField(allow_blank=True)
    highlights = serializers.CharField(allow_blank=True)
    description_origin = serializers.CharField()
    published_at = serializers.DateTimeField(allow_null=True)
    expires_at = serializers.DateTimeField(allow_null=True)
    property = PublicPropertySerializer()
    location = PublicLocationSerializer()
    price = PublicPriceSerializer(allow_null=True)
    advertiser = PublicAdvertiserSerializer()
    media = PublicMediaSerializer(many=True)


class PriceHistorySerializer(PublicPriceSerializer):
    created_at = serializers.DateTimeField()
    superseded_at = serializers.DateTimeField(allow_null=True)


class PublisherListingSerializer(PublicListingSerializer):
    id = serializers.UUIDField()
    status = serializers.CharField()
    property_id = serializers.UUIDField()
    property_reference = serializers.CharField()
    mandate_id = serializers.UUIDField()
    advertiser_id = serializers.UUIDField()
    advertising_organization_id = serializers.UUIDField(allow_null=True)
    ai_approved_at = serializers.DateTimeField(allow_null=True)
    moderation_hold = serializers.BooleanField()
    rejection_reason = serializers.CharField(allow_blank=True)
    submitted_at = serializers.DateTimeField(allow_null=True)
    paused_at = serializers.DateTimeField(allow_null=True)
    withdrawn_at = serializers.DateTimeField(allow_null=True)
    completed_at = serializers.DateTimeField(allow_null=True)
    rejected_at = serializers.DateTimeField(allow_null=True)
    created_at = serializers.DateTimeField()
    updated_at = serializers.DateTimeField()
    price_history = PriceHistorySerializer(many=True)


class ModerationListingSerializer(PublisherListingSerializer):
    moderation_note = serializers.CharField(allow_blank=True)
    moderation_notice = serializers.CharField()


class PriceWriteSerializer(serializers.Serializer):
    amount = serializers.JSONField(allow_null=True)
    currency = serializers.CharField()
    presentation = serializers.CharField()
    negotiable = serializers.BooleanField()
    billing_period = serializers.CharField()

    def validate_amount(self, value):
        if value is None:
            return None
        if isinstance(value, bool) or isinstance(value, float) or not isinstance(value, int):
            raise serializers.ValidationError(AMOUNT_INVALID)
        if value < 0:
            raise serializers.ValidationError(PRICE_INVALID)
        return value


class ListingWriteSerializer(serializers.Serializer):
    property_id = serializers.UUIDField(required=False)
    mandate_id = serializers.UUIDField(required=False)
    listing_type = serializers.CharField(required=False)
    title = serializers.CharField(required=False, allow_blank=False, max_length=200)
    description = serializers.CharField(required=False, allow_blank=True, max_length=8000)
    highlights = serializers.CharField(required=False, allow_blank=True, max_length=2000)
    description_origin = serializers.CharField(required=False)
    price = PriceWriteSerializer(required=False)

    def validate(self, attrs):
        if self.context.get("creating"):
            missing = {"property_id", "mandate_id", "listing_type", "title"} - set(attrs)
            if missing:
                raise serializers.ValidationError(
                    "A listing needs a property, mandate, type, and title."
                )
        return attrs


class ListingMediaWriteSerializer(serializers.Serializer):
    property_media_id = serializers.UUIDField()
    sort_order = serializers.IntegerField(required=False, min_value=0, max_value=1000)
    featured = serializers.BooleanField(required=False)

    def validate_sort_order(self, value):
        raw = self.initial_data.get("sort_order", 0)
        if isinstance(raw, bool):
            raise serializers.ValidationError("This media reference is not valid.")
        return value


class UploadSessionWriteSerializer(serializers.Serializer):
    kind = serializers.CharField()
    mime_type = serializers.CharField()
    size_bytes = serializers.IntegerField(min_value=1)
    caption = serializers.CharField(required=False, allow_blank=True, max_length=300)
    alt_text = serializers.CharField(required=False, allow_blank=True, max_length=300)

    def validate_size_bytes(self, value):
        raw = self.initial_data.get("size_bytes")
        if isinstance(raw, bool):
            raise serializers.ValidationError("This media reference is not valid.")
        return value


class UploadTargetSerializer(serializers.Serializer):
    url = serializers.CharField()
    method = serializers.CharField()
    headers = serializers.DictField(child=serializers.CharField())


class UploadSessionSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    media_id = serializers.UUIDField()
    status = serializers.CharField()
    expires_in = serializers.IntegerField()
    upload = UploadTargetSerializer()


class SignedUrlSerializer(serializers.Serializer):
    url = serializers.CharField()
    expires_in = serializers.IntegerField()


class PropertyMediaSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    property_id = serializers.UUIDField()
    kind = serializers.CharField()
    mime_type = serializers.CharField()
    asset_reference = serializers.CharField()
    upload_status = serializers.CharField()
    visibility = serializers.CharField()
    caption = serializers.CharField(allow_blank=True)
    alt_text = serializers.CharField(allow_blank=True)
    created_at = serializers.DateTimeField()


class MandateWriteSerializer(serializers.Serializer):
    property_id = serializers.UUIDField()
    listing_scope = serializers.CharField()
    representative_user_id = serializers.UUIDField(required=False, allow_null=True)
    representative_organization_id = serializers.UUIDField(required=False, allow_null=True)
    principal = serializers.CharField(required=False)
    valid_from = serializers.DateField(required=False)
    valid_until = serializers.DateField(required=False, allow_null=True)
    evidence_reference = serializers.CharField(required=False, allow_blank=True, max_length=200)


class MandateSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    property_id = serializers.UUIDField()
    listing_scope = serializers.CharField()
    status = serializers.CharField()
    self_declared = serializers.BooleanField()
    authority = serializers.CharField()
    authority_notice = serializers.CharField()
    valid_from = serializers.DateField()
    valid_until = serializers.DateField(allow_null=True)
    evidence_reference = serializers.CharField(allow_blank=True)
    principal_user_id = serializers.UUIDField(allow_null=True)
    principal_organization_id = serializers.UUIDField(allow_null=True)
    representative_user_id = serializers.UUIDField(allow_null=True)
    representative_organization_id = serializers.UUIDField(allow_null=True)
    granting_user_id = serializers.UUIDField()
    accepted_at = serializers.DateTimeField(allow_null=True)
    rejected_at = serializers.DateTimeField(allow_null=True)
    revoked_at = serializers.DateTimeField(allow_null=True)
    created_at = serializers.DateTimeField()


class ReasonSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=2000)


class ExpireDueSerializer(serializers.Serializer):
    expired = serializers.IntegerField()


class PublicEnquiryWriteSerializer(serializers.Serializer):
    """Public request fields. The service rejects anything else."""

    contact_name = serializers.CharField(max_length=160)
    contact_email = serializers.EmailField(max_length=254)
    contact_phone = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
        max_length=32,
    )
    message = serializers.CharField(max_length=5000)
    consent = serializers.BooleanField()


class PublicEnquirySerializer(serializers.Serializer):
    id = serializers.UUIDField()
    status = serializers.CharField()


class EnquiryAssignmentSerializer(serializers.Serializer):
    user_id = serializers.UUIDField()


class EnquiryInboxSerializer(serializers.Serializer):
    """Authorized enquiry read. The field set is the disclosure boundary."""

    id = serializers.UUIDField()
    listing_reference = serializers.CharField()
    contact_name = serializers.CharField(allow_null=True)
    contact_email = serializers.EmailField(allow_null=True)
    contact_phone = serializers.CharField(allow_null=True)
    message = serializers.CharField(allow_null=True)
    status = serializers.CharField()
    consent_at = serializers.DateTimeField()
    consent_version = serializers.CharField()
    created_at = serializers.DateTimeField()
    assignment = EnquiryAssignmentSerializer(allow_null=True)


class EnquiryAssignmentUpdateSerializer(serializers.Serializer):
    """Assign or unassign an enquiry. Setting null clears assignment."""

    user_id = serializers.UUIDField(allow_null=True, required=True)

    def validate(self, attrs):
        if hasattr(self, "initial_data") and isinstance(self.initial_data, dict):
            unknown = set(self.initial_data) - {"user_id"}
            if unknown:
                raise serializers.ValidationError("Unexpected fields are not allowed.")
        return attrs


class EnquiryTransitionWriteSerializer(serializers.Serializer):
    """Move an enquiry to its next status on the lifecycle graph."""

    status = serializers.ChoiceField(choices=EnquiryStatus.CHOICES)
    reason_code = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
        max_length=64,
    )

    def validate(self, attrs):
        if hasattr(self, "initial_data") and isinstance(self.initial_data, dict):
            unknown = set(self.initial_data) - {"status", "reason_code"}
            if unknown:
                raise serializers.ValidationError("Unexpected fields are not allowed.")
        return attrs
