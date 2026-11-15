"""HTTP adapters. Authorization is repeated in the services."""

from __future__ import annotations

import csv

from django.http import FileResponse, HttpResponse
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_protect
from rest_framework.pagination import PageNumberPagination
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.marketplace.throttles import EnquiryAddressThrottle, EnquiryCreationThrottle
from apps.solutions.domain import CatalogueStatus
from apps.solutions.errors import SolutionsError, raise_for_solutions
from apps.solutions.services import auctions, enquiries, records
from apps.solutions.services.access import ensure_staff
from apps.solutions.services.representation import (
    managed_auction,
    managed_enquiry,
    managed_investment,
    public_auction,
    public_category,
    public_created,
    public_investment,
    public_product,
    public_project,
    public_provider,
)


def _call(func, *args, **kwargs):
    try:
        return func(*args, **kwargs)
    except SolutionsError as exc:
        raise_for_solutions(exc)


def _page(request, queryset, represent):
    paginator = PageNumberPagination()
    paginator.page_size = 25
    paginator.page_size_query_param = "page_size"
    paginator.max_page_size = 100
    view = request.parser_context.get("view") if request.parser_context else None
    page = paginator.paginate_queryset(queryset, request, view=view)
    return paginator.get_paginated_response([represent(item) for item in page])


class PublicView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []


@method_decorator(csrf_protect, name="dispatch")
class PublicWriteView(PublicView):
    throttle_classes = [EnquiryCreationThrottle, EnquiryAddressThrottle]


@method_decorator(csrf_protect, name="dispatch")
class SessionView(APIView):
    permission_classes = [IsAuthenticated]


class AuctionCollectionView(PublicView):
    def get(self, request):
        queryset = auctions.public_auctions().prefetch_related("documents")
        category = request.query_params.get("category")
        county = request.query_params.get("county")
        if category:
            queryset = queryset.filter(category=category)
        if county:
            queryset = queryset.filter(county__iexact=county)
        return _page(request, queryset, public_auction)


class AuctionDetailView(PublicView):
    def get(self, request, reference):
        auction = _call(auctions.public_auction, reference)
        auction = auctions.public_auctions().prefetch_related("documents").get(pk=auction.pk)
        return Response(public_auction(auction))


class AuctionEnquiryView(PublicWriteView):
    def post(self, request, reference):
        auction = _call(auctions.public_auction, reference)
        enquiry, outcome = _call(
            enquiries.create_for_subject,
            subject=enquiries.subject_for_auction(auction),
            fields=request.data,
            request=request,
        )
        return Response(public_created(enquiry, outcome), status=201)


class ProjectCollectionView(PublicView):
    def get(self, request):
        queryset = records.public_projects()
        category = request.query_params.get("category")
        status = request.query_params.get("status")
        if category:
            queryset = queryset.filter(category=category)
        if status:
            queryset = queryset.filter(status=status)
        return _page(request, queryset, public_project)


class ProjectDetailView(PublicView):
    def get(self, request, slug):
        return Response(public_project(_call(records.public_project, slug)))


class ProjectEnquiryView(PublicWriteView):
    def post(self, request, slug):
        project = _call(records.public_project, slug)
        enquiry, outcome = _call(
            enquiries.create_for_subject,
            subject=enquiries.subject_for_project(project),
            fields=request.data,
            request=request,
        )
        return Response(public_created(enquiry, outcome), status=201)


class InsuranceCollectionView(PublicView):
    def get(self, request):
        queryset = records.public_products()
        line = request.query_params.get("line")
        category = request.query_params.get("category")
        if line:
            queryset = queryset.filter(line=line)
        if category:
            queryset = queryset.filter(category=category)
        return _page(request, queryset, public_product)


class InsuranceDetailView(PublicView):
    def get(self, request, slug):
        return Response(public_product(_call(records.public_product, slug)))


class InsuranceEnquiryView(PublicWriteView):
    def post(self, request, slug):
        product = _call(records.public_product, slug)
        enquiry, outcome = _call(
            enquiries.create_for_subject,
            subject=enquiries.subject_for_product(product),
            fields=request.data,
            request=request,
        )
        return Response(public_created(enquiry, outcome), status=201)


class InvestmentCollectionView(PublicView):
    def get(self, request):
        queryset = records.public_investments()
        category = request.query_params.get("category")
        if category:
            queryset = queryset.filter(category=category)
        return _page(request, queryset, public_investment)


class InvestmentDetailView(PublicView):
    def get(self, request, reference):
        return Response(public_investment(_call(records.public_investment, reference)))


class InvestmentEnquiryView(PublicWriteView):
    def post(self, request, reference):
        opportunity = _call(records.public_investment, reference)
        enquiry, outcome = _call(
            enquiries.create_for_subject,
            subject=enquiries.subject_for_investment(opportunity),
            fields=request.data,
            request=request,
        )
        return Response(public_created(enquiry, outcome), status=201)


class ServiceCollectionView(PublicView):
    def get(self, request):
        rows = [public_category(category, []) for category in records.public_categories()]
        return Response({"results": rows})


class ServiceDetailView(PublicView):
    def get(self, request, slug):
        category = _call(records.public_category, slug)
        providers = category.providers.filter(status=CatalogueStatus.PUBLISHED)
        return Response(public_category(category, providers))


class ServiceEnquiryView(PublicWriteView):
    def post(self, request, slug):
        category = _call(records.public_category, slug)
        enquiry, outcome = _call(
            enquiries.create_for_subject,
            subject=enquiries.subject_for_service(category),
            fields=request.data,
            request=request,
        )
        return Response(public_created(enquiry, outcome), status=201)


class ProviderEnquiryView(PublicWriteView):
    def post(self, request, provider_id):
        provider = _call(records.public_provider, provider_id)
        enquiry, outcome = _call(
            enquiries.create_for_subject,
            subject=enquiries.subject_for_service(provider.category, provider),
            fields=request.data,
            request=request,
        )
        return Response(public_created(enquiry, outcome), status=201)


class PublicDocumentView(PublicView):
    def get(self, request, document_id):
        document = _call(auctions.public_document, document_id)
        return FileResponse(
            document.upload.open("rb"),
            as_attachment=True,
            filename="document",
            content_type=document.content_type,
        )


class PrivateDocumentView(SessionView):
    def get(self, request, document_id):
        document = _call(auctions.private_document, actor=request.user, document_id=document_id)
        return FileResponse(
            document.upload.open("rb"),
            as_attachment=True,
            filename="document",
            content_type=document.content_type,
        )


class StaffAuctionCollectionView(SessionView):
    def get(self, request):
        _call(ensure_staff, request.user)
        queryset = auctions.AuctionListing.objects.prefetch_related("documents").order_by(
            "-created_at"
        )
        return _page(request, queryset, managed_auction)

    def post(self, request):
        auction = _call(auctions.create_staff_auction, actor=request.user, fields=request.data)
        return Response(managed_auction(auction), status=201)


class StaffAuctionDetailView(SessionView):
    def patch(self, request, reference):
        auction = _call(
            auctions.update_staff_auction,
            actor=request.user,
            reference=reference,
            fields=request.data,
        )
        return Response(managed_auction(auction))


class StaffAuctionStatusView(SessionView):
    def post(self, request, reference):
        status = request.data.get("status") if isinstance(request.data, dict) else None
        auction = _call(
            auctions.set_staff_status,
            actor=request.user,
            reference=reference,
            status=status,
        )
        return Response(managed_auction(auction))


class StaffProjectCollectionView(SessionView):
    def post(self, request):
        project = _call(records.create_project, actor=request.user, fields=request.data)
        return Response(public_project(project), status=201)


class StaffProjectDetailView(SessionView):
    def patch(self, request, slug):
        project = _call(
            records.update_project,
            actor=request.user,
            slug_value=slug,
            fields=request.data,
        )
        return Response(public_project(project))


class StaffInsuranceCollectionView(SessionView):
    def post(self, request):
        product = _call(records.create_insurance, actor=request.user, fields=request.data)
        return Response(public_product(product), status=201)


class StaffInsuranceDetailView(SessionView):
    def patch(self, request, slug):
        product = _call(
            records.update_insurance,
            actor=request.user,
            slug_value=slug,
            fields=request.data,
        )
        return Response(public_product(product))


class StaffInvestmentCollectionView(SessionView):
    def post(self, request):
        opportunity = _call(records.create_investment, actor=request.user, fields=request.data)
        return Response(managed_investment(opportunity), status=201)


class StaffInvestmentDetailView(SessionView):
    def patch(self, request, reference):
        opportunity = _call(
            records.update_investment,
            actor=request.user,
            reference=reference,
            fields=request.data,
        )
        return Response(managed_investment(opportunity))


class StaffProviderCollectionView(SessionView):
    def post(self, request):
        provider = _call(records.create_provider, actor=request.user, fields=request.data)
        return Response(public_provider(provider), status=201)


class StaffProviderDetailView(SessionView):
    def patch(self, request, provider_id):
        provider = _call(
            records.update_provider,
            actor=request.user,
            provider_id=provider_id,
            fields=request.data,
        )
        return Response(public_provider(provider))


class StaffEnquiryCollectionView(SessionView):
    def get(self, request):
        _call(ensure_staff, request.user)
        return _page(request, _filtered(enquiries.staff_queryset(), request), _staff_enquiry)


class StaffEnquiryExportView(SessionView):
    def get(self, request):
        _call(ensure_staff, request.user)
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = 'attachment; filename="glitters-enquiries.csv"'
        writer = csv.writer(response)
        writer.writerow(
            [
                "reference",
                "category",
                "status",
                "subject_reference",
                "assigned_organization_id",
                "source",
                "created_at",
                "contact_name",
                "contact_email",
                "preferred_contact",
            ]
        )
        for enquiry in _filtered(enquiries.staff_queryset(), request).iterator():
            writer.writerow(
                [
                    enquiry.reference,
                    enquiry.category,
                    enquiry.status,
                    enquiry.subject_reference,
                    enquiry.assigned_organization_id or "",
                    enquiry.source,
                    enquiry.created_at.isoformat(),
                    enquiry.contact_name or "",
                    enquiry.contact_email or "",
                    enquiry.preferred_contact,
                ]
            )
        return response


class StaffEnquiryDetailView(SessionView):
    def get(self, request, reference):
        enquiry = _call(enquiries.get_for_staff, request.user, reference)
        return Response(_staff_enquiry(enquiry))

    def patch(self, request, reference):
        enquiry = _call(
            enquiries.update_enquiry,
            actor=request.user,
            reference=reference,
            fields=request.data,
            staff=True,
            request=request,
        )
        return Response(_staff_enquiry(enquiry))


class PartnerAuctionCollectionView(SessionView):
    parser_classes = [JSONParser, FormParser, MultiPartParser]

    def get(self, request):
        queryset = auctions.partner_auctions(request.user).prefetch_related("documents")
        return _page(request, queryset, managed_auction)

    def post(self, request):
        auction = _call(auctions.create_partner_auction, actor=request.user, fields=request.data)
        return Response(managed_auction(auction), status=201)


class PartnerAuctionDetailView(SessionView):
    def patch(self, request, reference):
        auction = _call(
            auctions.update_partner_auction,
            actor=request.user,
            reference=reference,
            fields=request.data,
        )
        return Response(managed_auction(auction))


class PartnerAuctionSubmitView(SessionView):
    def post(self, request, reference):
        auction = _call(auctions.set_partner_status, actor=request.user, reference=reference)
        return Response(managed_auction(auction))


class _DocumentUploadView(SessionView):
    parser_classes = [MultiPartParser, FormParser]
    staff = False

    def post(self, request, reference):
        upload = request.FILES.get("upload")
        content_type = request.data.get("content_type") or getattr(upload, "content_type", "")
        document = _call(
            auctions.store_document,
            actor=request.user,
            reference=reference,
            upload=upload,
            content_type=content_type,
            kind=request.data.get("kind"),
            visibility=request.data.get("visibility") or "private",
            caption=request.data.get("caption") or "",
            staff=self.staff,
        )
        return Response({"id": str(document.id), "visibility": document.visibility}, status=201)


class StaffDocumentUploadView(_DocumentUploadView):
    staff = True


class PartnerDocumentUploadView(_DocumentUploadView):
    staff = False


class PartnerEnquiryCollectionView(SessionView):
    def get(self, request):
        return _page(request, enquiries.partner_queryset(request.user), _partner_enquiry)


class PartnerEnquiryDetailView(SessionView):
    def get(self, request, reference):
        enquiry = _call(enquiries.get_for_partner, request.user, reference)
        return Response(_partner_enquiry(enquiry))

    def patch(self, request, reference):
        enquiry = _call(
            enquiries.update_enquiry,
            actor=request.user,
            reference=reference,
            fields=request.data,
            staff=False,
            request=request,
        )
        return Response(_partner_enquiry(enquiry))


def _filtered(queryset, request):
    category = request.query_params.get("category")
    status = request.query_params.get("status")
    if category:
        queryset = queryset.filter(category=category)
    if status:
        queryset = queryset.filter(status=status)
    return queryset


def _staff_enquiry(enquiry):
    return managed_enquiry(enquiry, staff=True)


def _partner_enquiry(enquiry):
    return managed_enquiry(enquiry, staff=False)
