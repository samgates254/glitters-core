from django.urls import path

from apps.construction.views import (
    BoQCollectionView,
    InspectionCollectionView,
    MilestoneCollectionView,
    ProjectCollectionView,
    ProjectDetailView,
    ProjectReportView,
    ProjectTransitionView,
    SiteDiaryApprovalView,
    SiteDiaryCollectionView,
    TechnicalDocumentDownloadView,
)

urlpatterns = [
    path("projects/", ProjectCollectionView.as_view(), name="construction-projects"),
    path(
        "projects/<uuid:project_id>/",
        ProjectDetailView.as_view(),
        name="construction-project-detail",
    ),
    path(
        "projects/<uuid:project_id>/transitions/",
        ProjectTransitionView.as_view(),
        name="construction-project-transitions",
    ),
    path(
        "projects/<uuid:project_id>/inspections/",
        InspectionCollectionView.as_view(),
        name="construction-inspections",
    ),
    path("projects/<uuid:project_id>/boqs/", BoQCollectionView.as_view(), name="construction-boqs"),
    path(
        "projects/<uuid:project_id>/milestones/",
        MilestoneCollectionView.as_view(),
        name="construction-milestones",
    ),
    path(
        "projects/<uuid:project_id>/reports/",
        ProjectReportView.as_view(),
        name="construction-project-report",
    ),
    path(
        "projects/<uuid:project_id>/site-diaries/",
        SiteDiaryCollectionView.as_view(),
        name="construction-site-diaries",
    ),
    path(
        "site-diaries/<uuid:diary_id>/approval/",
        SiteDiaryApprovalView.as_view(),
        name="construction-site-diary-approval",
    ),
    path(
        "documents/<uuid:document_id>/download/",
        TechnicalDocumentDownloadView.as_view(),
        name="construction-document-download",
    ),
]
