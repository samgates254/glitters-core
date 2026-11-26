from django.http import FileResponse, HttpResponse
from django.shortcuts import get_object_or_404
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.construction.errors import ConstructionError, raise_for_construction
from apps.construction.models import (
    ConstructionProject,
    TechnicalDocument,
)
from apps.construction.permissions import has_org_access
from apps.construction.serializers import (
    BoQSerializer,
    InspectionSerializer,
    MilestoneSerializer,
    ProjectSerializer,
)
from apps.construction.services import (
    approve_site_diary,
    create_inspection,
    create_project,
    create_site_diary,
    project_report,
    project_report_csv,
    transition_project,
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


class BoQCollectionView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, project_id):
        project = get_object_or_404(ConstructionProject, id=project_id)
        if not has_org_access(request.user, project.organization):
            return Response({"detail": "Not found."}, status=404)
        return Response(BoQSerializer(project.boqs.all(), many=True).data)


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
        output_format = request.query_params.get("format", "json").lower()
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
