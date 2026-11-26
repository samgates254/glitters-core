from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.construction.errors import ConstructionError, raise_for_construction
from apps.construction.models import (
    ApprovedVariation,
    BoQ,
    BoQItem,
    BoQSection,
    BudgetCategory,
    ConstructionProject,
    CostRecord,
    ProjectBudget,
    TechnicalDocument,
)
from apps.construction.permissions import has_org_access
from apps.construction.serializers import (
    BoQCreateSerializer,
    BoQItemSerializer,
    BoQSectionSerializer,
    BoQSectionWriteSerializer,
    BoQSerializer,
    BudgetCategorySerializer,
    BudgetCreateSerializer,
    BudgetSerializer,
    CostRecordSerializer,
    CostRecordWriteSerializer,
    InspectionSerializer,
    MilestoneSerializer,
    ProjectSerializer,
    ReasonSerializer,
    VariationSerializer,
    VariationWriteSerializer,
)
from apps.construction.services import (
    approve_boq,
    approve_budget,
    approve_site_diary,
    approve_variation,
    boq_csv_export,
    boq_xlsx_export,
    create_boq,
    create_boq_item,
    create_boq_section,
    create_budget_category,
    create_inspection,
    create_project,
    create_project_budget,
    create_site_diary,
    delete_boq_item,
    project_cost_report,
    project_cost_report_csv,
    project_report,
    project_report_csv,
    record_project_cost,
    reject_variation,
    request_variation,
    review_boq,
    review_variation,
    revise_boq,
    revise_budget,
    rows_xlsx_export,
    submit_boq,
    submit_budget,
    transition_project,
    update_boq_item,
)


def call_service(func, *args, **kwargs):
    try:
        return func(*args, **kwargs)
    except ConstructionError as exc:
        raise_for_construction(exc)


class ProjectCollectionView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        projects = ConstructionProject.objects.filter(
            organization__memberships__user=request.user,
            organization__memberships__status="active",
        ).distinct()
        return Response(ProjectSerializer(projects, many=True).data)

    def post(self, request):
        org_id = request.data.get("organization")
        from apps.organizations.models import Organization

        organization = get_object_or_404(Organization, id=org_id)
        created = call_service(
            create_project,
            actor=request.user,
            organization=organization,
            code=request.data["code"],
            name=request.data["name"],
            classification=request.data["classification"],
            description=request.data.get("description", ""),
            budget=request.data.get("budget", 0),
            start_date=request.data.get("start_date"),
            expected_completion_date=request.data.get("expected_completion_date"),
        )
        return Response(ProjectSerializer(created).data, status=201)


class ProjectDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, project_id):
        project = get_object_or_404(ConstructionProject, id=project_id)
        if not has_org_access(request.user, project.organization):
            return Response({"detail": "Not found."}, status=404)
        return Response(ProjectSerializer(project).data)


class ProjectTransitionView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, project_id):
        project = get_object_or_404(ConstructionProject, id=project_id)
        changed = call_service(
            transition_project,
            actor=request.user,
            project=project,
            target_status=request.data["status"],
        )
        return Response(ProjectSerializer(changed).data)


class InspectionCollectionView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, project_id):
        project = get_object_or_404(ConstructionProject, id=project_id)
        if not has_org_access(request.user, project.organization):
            return Response({"detail": "Not found."}, status=404)
        return Response(InspectionSerializer(project.inspections.all(), many=True).data)

    def post(self, request, project_id):
        project = get_object_or_404(ConstructionProject, id=project_id)
        inspection = call_service(
            create_inspection,
            actor=request.user,
            project=project,
            title=request.data["title"],
            inspector=request.user,
            inspection_date=request.data["inspection_date"],
            observations=request.data.get("observations", ""),
            safety_observations=request.data.get("safety_observations", ""),
        )
        return Response(InspectionSerializer(inspection).data, status=201)


class ConstructionPagination(PageNumberPagination):
    page_size = 25
    page_size_query_param = "page_size"
    max_page_size = 100


def authorized_project_or_404(user, project_id):
    project = get_object_or_404(ConstructionProject, id=project_id)
    if not has_org_access(user, project.organization):
        raise Http404
    return project


def authorized_boq_or_404(user, project_id, boq_id):
    project = authorized_project_or_404(user, project_id)
    return get_object_or_404(BoQ, id=boq_id, project=project)


class BoQCollectionView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, project_id):
        project = authorized_project_or_404(request.user, project_id)
        queryset = project.boqs.order_by("-revision", "-created_at")
        paginator = ConstructionPagination()
        page = paginator.paginate_queryset(queryset, request, view=self)
        return paginator.get_paginated_response(BoQSerializer(page, many=True).data)

    def post(self, request, project_id):
        project = authorized_project_or_404(request.user, project_id)
        serializer = BoQCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        boq = call_service(
            create_boq,
            actor=request.user,
            project=project,
            **serializer.validated_data,
        )
        return Response(BoQSerializer(boq).data, status=201)


class BoQSectionCollectionView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, project_id, boq_id):
        boq = authorized_boq_or_404(request.user, project_id, boq_id)
        return Response(BoQSectionSerializer(boq.sections.order_by("sequence"), many=True).data)

    def post(self, request, project_id, boq_id):
        boq = authorized_boq_or_404(request.user, project_id, boq_id)
        serializer = BoQSectionWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        values = serializer.validated_data
        parent = None
        if values.get("parent"):
            parent = get_object_or_404(BoQSection, id=values["parent"], boq=boq)
        section = call_service(
            create_boq_section,
            actor=request.user,
            boq=boq,
            code=values["code"],
            title=values["title"],
            sequence=values["sequence"],
            parent=parent,
        )
        return Response(BoQSectionSerializer(section).data, status=201)


class BoQItemCollectionView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, project_id, boq_id, section_id):
        boq = authorized_boq_or_404(request.user, project_id, boq_id)
        section = get_object_or_404(BoQSection, id=section_id, boq=boq)
        return Response(BoQItemSerializer(section.items.order_by("item_code"), many=True).data)

    def post(self, request, project_id, boq_id, section_id):
        boq = authorized_boq_or_404(request.user, project_id, boq_id)
        section = get_object_or_404(BoQSection, id=section_id, boq=boq)
        serializer = BoQItemSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        item = call_service(
            create_boq_item,
            actor=request.user,
            section=section,
            **serializer.validated_data,
        )
        return Response(BoQItemSerializer(item).data, status=201)


class BoQItemDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def patch(self, request, project_id, boq_id, section_id, item_id):
        boq = authorized_boq_or_404(request.user, project_id, boq_id)
        section = get_object_or_404(BoQSection, id=section_id, boq=boq)
        item = get_object_or_404(BoQItem, id=item_id, section=section)
        serializer = BoQItemSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        updated = call_service(
            update_boq_item, actor=request.user, item=item, **serializer.validated_data
        )
        return Response(BoQItemSerializer(updated).data)

    def delete(self, request, project_id, boq_id, section_id, item_id):
        boq = authorized_boq_or_404(request.user, project_id, boq_id)
        section = get_object_or_404(BoQSection, id=section_id, boq=boq)
        item = get_object_or_404(BoQItem, id=item_id, section=section)
        call_service(delete_boq_item, actor=request.user, item=item)
        return Response(status=204)


class BoQWorkflowView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, project_id, boq_id, action):
        boq = authorized_boq_or_404(request.user, project_id, boq_id)
        actions = {
            "submit": submit_boq,
            "review": review_boq,
            "approve": approve_boq,
        }
        if action not in actions:
            return Response({"detail": "Unknown BoQ workflow action."}, status=404)
        result = call_service(actions[action], actor=request.user, boq=boq)
        return Response(BoQSerializer(result).data)


class BoQRevisionView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, project_id, boq_id):
        boq = authorized_boq_or_404(request.user, project_id, boq_id)
        revision = call_service(revise_boq, actor=request.user, boq=boq)
        return Response(BoQSerializer(revision).data, status=201)


class BoQExportView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, project_id, boq_id):
        boq = authorized_boq_or_404(request.user, project_id, boq_id)
        output_format = request.query_params.get("output", "csv").lower()
        if output_format == "csv":
            response = HttpResponse(boq_csv_export(boq), content_type="text/csv; charset=utf-8")
            response["Content-Disposition"] = f'attachment; filename="boq-{boq.id}.csv"'
            return response
        if output_format == "xlsx":
            response = HttpResponse(
                boq_xlsx_export(boq),
                content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
            response["Content-Disposition"] = f'attachment; filename="boq-{boq.id}.xlsx"'
            return response
        return Response(
            {
                "detail": (
                    "Supported BoQ export formats are csv and xlsx; PDF renderer is not installed."
                )
            },
            status=406,
        )


class BudgetCollectionView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, project_id):
        project = authorized_project_or_404(request.user, project_id)
        queryset = project.budgets.order_by("-revision", "-created_at")
        paginator = ConstructionPagination()
        page = paginator.paginate_queryset(queryset, request, view=self)
        return paginator.get_paginated_response(BudgetSerializer(page, many=True).data)

    def post(self, request, project_id):
        project = authorized_project_or_404(request.user, project_id)
        serializer = BudgetCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        budget = call_service(
            create_project_budget,
            actor=request.user,
            project=project,
            **serializer.validated_data,
        )
        return Response(BudgetSerializer(budget).data, status=201)


def authorized_budget_or_404(user, project_id, budget_id):
    project = authorized_project_or_404(user, project_id)
    return get_object_or_404(ProjectBudget, id=budget_id, project=project)


class BudgetCategoryCollectionView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, project_id, budget_id):
        budget = authorized_budget_or_404(request.user, project_id, budget_id)
        return Response(
            BudgetCategorySerializer(budget.categories.order_by("name"), many=True).data
        )

    def post(self, request, project_id, budget_id):
        budget = authorized_budget_or_404(request.user, project_id, budget_id)
        serializer = BudgetCategorySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        category = call_service(
            create_budget_category,
            actor=request.user,
            budget=budget,
            name=serializer.validated_data["name"],
            planned_amount=serializer.validated_data["planned_amount"],
        )
        return Response(BudgetCategorySerializer(category).data, status=201)


class BudgetWorkflowView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, project_id, budget_id, action):
        budget = authorized_budget_or_404(request.user, project_id, budget_id)
        actions = {"submit": submit_budget, "approve": approve_budget}
        if action == "revise":
            result = call_service(revise_budget, actor=request.user, budget=budget)
            return Response(BudgetSerializer(result).data, status=201)
        if action not in actions:
            return Response({"detail": "Unknown budget workflow action."}, status=404)
        result = call_service(actions[action], actor=request.user, budget=budget)
        return Response(BudgetSerializer(result).data)


class CostRecordCollectionView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, project_id, budget_id):
        budget = authorized_budget_or_404(request.user, project_id, budget_id)
        records = CostRecord.objects.filter(
            project=budget.project, category__budget=budget
        ).order_by("-recorded_at")
        paginator = ConstructionPagination()
        page = paginator.paginate_queryset(records, request, view=self)
        return paginator.get_paginated_response(CostRecordSerializer(page, many=True).data)

    def post(self, request, project_id, budget_id):
        budget = authorized_budget_or_404(request.user, project_id, budget_id)
        serializer = CostRecordWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        category = get_object_or_404(
            BudgetCategory,
            id=serializer.validated_data["category"],
            budget=budget,
        )
        values = serializer.validated_data
        record = call_service(
            record_project_cost,
            actor=request.user,
            category=category,
            record_type=values["record_type"],
            amount=values["amount"],
            reference=values["reference"],
            description=values.get("description", ""),
        )
        return Response(CostRecordSerializer(record).data, status=201)


class VariationCollectionView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, project_id):
        project = authorized_project_or_404(request.user, project_id)
        queryset = project.variations.order_by("-created_at")
        paginator = ConstructionPagination()
        page = paginator.paginate_queryset(queryset, request, view=self)
        return paginator.get_paginated_response(VariationSerializer(page, many=True).data)

    def post(self, request, project_id):
        project = authorized_project_or_404(request.user, project_id)
        serializer = VariationWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        values = serializer.validated_data
        category = None
        if values.get("budget_category"):
            category = get_object_or_404(
                BudgetCategory,
                id=values["budget_category"],
                budget__project=project,
            )
        variation = call_service(
            request_variation,
            actor=request.user,
            project=project,
            reference=values["reference"],
            reason=values["reason"],
            scope_modification=values["scope_modification"],
            cost_impact=values["cost_impact"],
            time_impact_days=values["time_impact_days"],
            budget_category=category,
        )
        return Response(VariationSerializer(variation).data, status=201)


class VariationWorkflowView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, project_id, variation_id, action):
        project = authorized_project_or_404(request.user, project_id)
        variation = get_object_or_404(ApprovedVariation, id=variation_id, project=project)
        if action == "review":
            result = call_service(review_variation, actor=request.user, variation=variation)
        elif action == "approve":
            result = call_service(approve_variation, actor=request.user, variation=variation)
        elif action == "reject":
            serializer = ReasonSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            result = call_service(
                reject_variation,
                actor=request.user,
                variation=variation,
                reason=serializer.validated_data["reason"],
            )
        else:
            return Response({"detail": "Unknown variation workflow action."}, status=404)
        return Response(VariationSerializer(result).data)


class ProjectCostReportView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, project_id):
        project = authorized_project_or_404(request.user, project_id)
        report = project_cost_report(project)
        output_format = request.query_params.get("output", "json").lower()
        if output_format == "json":
            return Response(report)
        if output_format == "csv":
            response = HttpResponse(
                project_cost_report_csv(project), content_type="text/csv; charset=utf-8"
            )
            response["Content-Disposition"] = (
                f'attachment; filename="construction-cost-report-{project.id}.csv"'
            )
            return response
        if output_format == "xlsx":
            rows = [
                ["Project cost report", report["project_id"], report["currency"]],
                ["approved_budget", report["approved_budget"]],
                ["approved_variations", report["approved_variations"]],
                ["current_budget", report["current_budget"]],
                ["committed_costs", report["committed_costs"]],
                ["actual_costs", report["actual_costs"]],
                ["forecast_inputs", report["forecast_inputs"]],
                [],
                ["category", "planned", "committed", "actual", "remaining_to_plan"],
            ]
            rows.extend(
                [
                    item["category"],
                    item["planned"],
                    item["committed"],
                    item["actual"],
                    item["remaining_to_plan"],
                ]
                for item in report["categories"]
            )
            response = HttpResponse(
                rows_xlsx_export(rows),
                content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
            response["Content-Disposition"] = (
                f'attachment; filename="construction-cost-report-{project.id}.xlsx"'
            )
            return response
        return Response(
            {"detail": "Supported cost report formats are json, csv, and xlsx."},
            status=406,
        )


class MilestoneCollectionView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, project_id):
        project = get_object_or_404(ConstructionProject, id=project_id)
        if not has_org_access(request.user, project.organization):
            return Response({"detail": "Not found."}, status=404)
        return Response(MilestoneSerializer(project.milestones.all(), many=True).data)


class ProjectReportView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, project_id):
        project = get_object_or_404(ConstructionProject, id=project_id)
        if not has_org_access(request.user, project.organization):
            return Response({"detail": "Not found."}, status=404)
        return Response(project_report(project))

    def post(self, request, project_id):
        project = get_object_or_404(ConstructionProject, id=project_id)
        if not has_org_access(request.user, project.organization):
            return Response({"detail": "Not found."}, status=404)
        output_format = request.query_params.get("output", "json").lower()
        if output_format == "csv":
            response = HttpResponse(project_report_csv(project), content_type="text/csv")
            response["Content-Disposition"] = (
                f'attachment; filename="construction-project-{project.code}.csv"'
            )
            return response
        return Response(project_report(project))


class SiteDiaryCollectionView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, project_id):
        project = get_object_or_404(ConstructionProject, id=project_id)
        if not has_org_access(request.user, project.organization):
            return Response({"detail": "Not found."}, status=404)
        return Response(
            [
                {
                    "id": str(diary.id),
                    "diary_date": diary.diary_date.isoformat(),
                    "work_completed": diary.work_completed,
                    "approved_at": diary.approved_at.isoformat() if diary.approved_at else None,
                }
                for diary in project.site_diaries.all()
            ]
        )

    def post(self, request, project_id):
        project = get_object_or_404(ConstructionProject, id=project_id)
        allowed_fields = {
            "weather_and_conditions",
            "work_completed",
            "labor_summary",
            "contractor_activity",
            "equipment_usage",
            "material_deliveries",
            "delays",
            "instructions",
            "safety_observations",
        }
        fields = {key: value for key, value in request.data.items() if key in allowed_fields}
        diary = call_service(
            create_site_diary,
            actor=request.user,
            project=project,
            diary_date=request.data["diary_date"],
            **fields,
        )
        return Response(
            {
                "id": str(diary.id),
                "diary_date": diary.diary_date.isoformat(),
                "work_completed": diary.work_completed,
            },
            status=201,
        )


class SiteDiaryApprovalView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, diary_id):
        from apps.construction.models import SiteDiary

        diary = get_object_or_404(SiteDiary, id=diary_id)
        if not has_org_access(request.user, diary.project.organization):
            return Response({"detail": "Not found."}, status=404)
        approved = call_service(approve_site_diary, actor=request.user, diary=diary)
        return Response(
            {
                "id": str(approved.id),
                "approved_at": approved.approved_at.isoformat(),
            }
        )


class TechnicalDocumentDownloadView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, document_id):
        document = get_object_or_404(TechnicalDocument, id=document_id)
        if not has_org_access(request.user, document.project.organization):
            return Response({"detail": "Not found."}, status=404)
        if not document.file:
            return Response({"detail": "File is unavailable."}, status=404)
        return FileResponse(
            document.file.open("rb"),
            as_attachment=True,
            filename=document.file.name.rsplit("/", 1)[-1],
        )
