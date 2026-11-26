from django.http import FileResponse
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
    create_inspection,
    create_project,
    project_report,
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
