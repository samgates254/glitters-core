from decimal import Decimal

from rest_framework import serializers

from apps.construction.models import (
    RFI,
    ApprovedVariation,
    BoQ,
    BoQItem,
    BoQSection,
    BudgetCategory,
    ConstructionProject,
    CostRecord,
    Defect,
    EngineeringInspection,
    ProjectBudget,
    ProjectMilestone,
    TechnicalDocument,
    Tender,
)


class StrictDecimalField(serializers.DecimalField):
    def to_internal_value(self, data):
        parsed = super().to_internal_value(data)
        try:
            original = Decimal(str(data))
        except TypeError, ValueError, ArithmeticError:
            return parsed
        if original != parsed:
            raise serializers.ValidationError(
                f"Ensure no more than {self.decimal_places} decimal places are supplied."
            )
        return parsed


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
            "reference",
            "revision",
            "revision_of",
            "status",
            "subtotal",
            "tax_amount",
            "contingency_amount",
            "grand_total",
            "approved_at",
            "approved_by",
            "submitted_by",
            "submitted_at",
            "reviewed_by",
            "reviewed_at",
            "created_by",
            "created_at",
        ]


class BoQSectionSerializer(serializers.ModelSerializer):
    class Meta:
        model = BoQSection
        fields = ["id", "boq", "parent", "code", "title", "sequence"]
        read_only_fields = ["id", "boq"]


class BoQItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = BoQItem
        fields = [
            "id",
            "section",
            "item_code",
            "description",
            "unit",
            "quantity",
            "unit_rate",
            "amount",
        ]
        read_only_fields = ["id", "section", "amount"]

    quantity = StrictDecimalField(max_digits=16, decimal_places=4, min_value=0)
    unit_rate = StrictDecimalField(max_digits=16, decimal_places=4, min_value=0)


class BoQCreateSerializer(serializers.Serializer):
    title = serializers.CharField(max_length=200)
    reference = serializers.CharField(max_length=32, required=False, allow_blank=True)
    currency = serializers.RegexField(r"^[A-Z]{3}$", default="KES")
    tax_percent = StrictDecimalField(
        max_digits=5, decimal_places=2, min_value=0, max_value=100, default=0
    )
    contingency_percent = StrictDecimalField(
        max_digits=5, decimal_places=2, min_value=0, max_value=100, default=0
    )


class BoQSectionWriteSerializer(serializers.Serializer):
    code = serializers.CharField(max_length=32)
    title = serializers.CharField(max_length=200)
    sequence = serializers.IntegerField(min_value=1, default=1)
    parent = serializers.UUIDField(required=False, allow_null=True)


class BudgetSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProjectBudget
        fields = [
            "id",
            "project",
            "name",
            "revision",
            "revision_of",
            "currency",
            "status",
            "total",
            "approved_at",
            "approved_by",
            "created_by",
            "created_at",
        ]
        read_only_fields = [
            "id",
            "revision",
            "revision_of",
            "status",
            "total",
            "approved_at",
            "approved_by",
            "created_by",
            "created_at",
        ]


class BudgetCreateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=200)
    currency = serializers.RegexField(r"^[A-Z]{3}$", default="KES")


class BudgetCategorySerializer(serializers.ModelSerializer):
    planned_amount = StrictDecimalField(max_digits=16, decimal_places=2, min_value=0)

    class Meta:
        model = BudgetCategory
        fields = [
            "id",
            "budget",
            "name",
            "planned_amount",
            "committed_amount",
            "actual_amount",
        ]
        read_only_fields = ["id", "budget", "committed_amount", "actual_amount"]


class CostRecordSerializer(serializers.ModelSerializer):
    amount = StrictDecimalField(max_digits=16, decimal_places=2, min_value=0)

    class Meta:
        model = CostRecord
        fields = [
            "id",
            "project",
            "category",
            "record_type",
            "amount",
            "reference",
            "description",
            "recorded_by",
            "recorded_at",
        ]
        read_only_fields = ["id", "project", "recorded_by", "recorded_at"]


class CostRecordWriteSerializer(serializers.Serializer):
    category = serializers.UUIDField()
    record_type = serializers.ChoiceField(choices=["commitment", "actual", "forecast"])
    amount = StrictDecimalField(max_digits=16, decimal_places=2, min_value=0)
    reference = serializers.CharField(max_length=64)
    description = serializers.CharField(required=False, allow_blank=True)


class VariationWriteSerializer(serializers.Serializer):
    reference = serializers.CharField(max_length=32)
    reason = serializers.CharField()
    scope_modification = serializers.CharField()
    cost_impact = StrictDecimalField(max_digits=16, decimal_places=2)
    time_impact_days = serializers.IntegerField(min_value=0, default=0)
    budget_category = serializers.UUIDField(required=False, allow_null=True)


class ReasonSerializer(serializers.Serializer):
    reason = serializers.CharField()


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
