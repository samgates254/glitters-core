from rest_framework import serializers

from apps.construction.models import (
    RFI,
    ApprovedVariation,
    BoQ,
    BoQItem,
    BoQSection,
    ConstructionProject,
    Defect,
    EngineeringInspection,
    ProjectMilestone,
    TechnicalDocument,
    Tender,
)


class ProjectSerializer(serializers.ModelSerializer):
    class Meta:
        model = ConstructionProject
        fields = [
            "id",
            "organization",
            "property",
            "code",
            "name",
            "classification",
            "description",
            "site_location_reference",
            "client_name",
            "budget",
            "start_date",
            "expected_completion_date",
            "status",
            "project_manager",
            "created_by",
            "version",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "status", "created_by", "version", "created_at", "updated_at"]


class InspectionSerializer(serializers.ModelSerializer):
    class Meta:
        model = EngineeringInspection
        fields = "__all__"
        read_only_fields = ["id", "status", "report_approved_by", "submitted_at", "created_at"]


class DefectSerializer(serializers.ModelSerializer):
    class Meta:
        model = Defect
        fields = "__all__"
        read_only_fields = ["id", "status", "closed_by", "closed_at", "created_at"]


class BoQSerializer(serializers.ModelSerializer):
    class Meta:
        model = BoQ
        fields = "__all__"
        read_only_fields = [
            "id",
            "revision",
            "status",
            "subtotal",
            "tax_amount",
            "contingency_amount",
            "grand_total",
            "approved_at",
            "approved_by",
            "created_by",
            "created_at",
        ]


class BoQSectionSerializer(serializers.ModelSerializer):
    class Meta:
        model = BoQSection
        fields = "__all__"


class BoQItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = BoQItem
        fields = "__all__"
        read_only_fields = ["id", "amount"]


class VariationSerializer(serializers.ModelSerializer):
    class Meta:
        model = ApprovedVariation
        fields = "__all__"
        read_only_fields = ["id", "status", "approved_by", "approved_at", "created_at"]


class MilestoneSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProjectMilestone
        fields = "__all__"
        read_only_fields = ["id", "completed_confirmed_by", "created_at"]


class RFISerializer(serializers.ModelSerializer):
    class Meta:
        model = RFI
        fields = "__all__"
        read_only_fields = ["id", "status", "submitted_at", "closed_at", "created_at"]


class TenderSerializer(serializers.ModelSerializer):
    class Meta:
        model = Tender
        fields = "__all__"
        read_only_fields = ["id", "status", "awarded_contractor", "created_by", "created_at"]


class TechnicalDocumentSerializer(serializers.ModelSerializer):
    class Meta:
        model = TechnicalDocument
        fields = "__all__"
        read_only_fields = [
            "id",
            "status",
            "integrity_digest",
            "uploaded_by",
            "approved_by",
            "approved_at",
            "created_at",
        ]
