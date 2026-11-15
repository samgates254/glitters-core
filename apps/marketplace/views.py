"""HTTP adapters for the marketplace. Authorization is repeated in the services."""

from __future__ import annotations

from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_protect
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.marketplace.errors import MarketplaceError, raise_for_marketplace
from apps.marketplace.filters import parse_enquiry_filters, parse_listing_filters
from apps.marketplace.serializers import (
    EnquiryAssignmentUpdateSerializer,
    EnquiryInboxSerializer,
    EnquiryTransitionWriteSerializer,
    ExpireDueSerializer,
    ListingMediaWriteSerializer,
    ListingWriteSerializer,
    MandateSerializer,
    MandateWriteSerializer,
    ModerationListingSerializer,
    PropertyMediaSerializer,
    PublicEnquirySerializer,
    PublicEnquiryWriteSerializer,
    PublicListingSerializer,
    PublisherListingSerializer,
    ReasonSerializer,
    SignedUrlSerializer,
    UploadSessionSerializer,
    UploadSessionWriteSerializer,
)
from apps.marketplace.services.discovery import publisher_listings, search_public
from apps.marketplace.services.enquiries import (
    assign_enquiry,
    create_public_enquiry,
    get_authorized_enquiry,
    list_enquiries,
    transition_enquiry,
)
from apps.marketplace.services.listings import (
    accept_ai_description,
    approve_listing,
    attach_media,
    complete_listing,
    create_listing,
    detach_media,
    expire_due,
    expire_listing,
    load_for_staff,
    load_managed,
    pause_listing,
    public_listing,
    reinstate_listing,
    reject_listing,
    renew_listing,
    resume_listing,
    revise_listing,
    submit_listing,
    suspend_listing,
    update_listing,
    withdraw_listing,
)
from apps.marketplace.services.mandates import (
    accept_mandate,
    create_mandate,
    get_mandate_for_actor,
    list_mandates,
    reject_mandate,
    revoke_mandate,
)
from apps.marketplace.services.media import (
    list_property_media,
    remove_property_media,
)
from apps.marketplace.services.representation import (
    enquiry_inbox,
    mandate_payload,
    moderation_listing,
    property_media_payload,
    publisher_listing,
)
from apps.marketplace.services.representation import (
    public_listing as public_payload,
)
from apps.marketplace.services.uploads import (
    cancel_upload,
    complete_upload,
    create_upload,
    download_property_media,
    public_listing_photo,
    reject_client_storage_claims,
)
from apps.marketplace.throttles import EnquiryAddressThrottle, EnquiryCreationThrottle
from apps.organizations.errors import OrganizationError, raise_for_organization
from apps.properties.errors import PropertyError, raise_for_property


def _call(func, *args, **kwargs):
    try:
        return func(*args, **kwargs)
    except MarketplaceError as exc:
        raise_for_marketplace(exc)
    except PropertyError as exc:
        raise_for_property(exc)
    except OrganizationError as exc:
        raise_for_organization(exc)


def _body(serializer_class, data, *, context=None):
    serializer = serializer_class(data=data, context=context or {})
    serializer.is_valid(raise_exception=True)
    return serializer.validated_data


def _page(request, queryset, represent, serializer_class):
    from rest_framework.pagination import PageNumberPagination

    paginator = PageNumberPagination()
    paginator.page_size = 25
    paginator.page_size_query_param = "page_size"
    paginator.max_page_size = 100
    page = paginator.paginate_queryset(queryset, request, view=request.parser_context.get("view"))
    rows = [represent(item) for item in page]
    return paginator.get_paginated_response(serializer_class(rows, many=True).data)


class PublicMarketplaceView(APIView):
    permission_classes = [AllowAny]


@method_decorator(csrf_protect, name="dispatch")
class PublisherView(APIView):
    permission_classes = [IsAuthenticated]


class PublicListingCollectionView(PublicMarketplaceView):
    @extend_schema(
        parameters=[
            OpenApiParameter(name="listing_type", type=str, required=False),
            OpenApiParameter(name="property_type", type=str, required=False),
            OpenApiParameter(name="country", type=str, required=False),
            OpenApiParameter(name="administrative_area_id", type=str, required=False),
            OpenApiParameter(name="locality_id", type=str, required=False),
            OpenApiParameter(name="price_min", type=int, required=False),
            OpenApiParameter(name="price_max", type=int, required=False),
            OpenApiParameter(name="currency", type=str, required=False),
            OpenApiParameter(name="bedrooms", type=int, required=False),
            OpenApiParameter(name="bathrooms", type=int, required=False),
            OpenApiParameter(name="floor_area_min", type=str, required=False),
            OpenApiParameter(name="land_area_min", type=str, required=False),
            OpenApiParameter(name="published_after", type=str, required=False),
            OpenApiParameter(name="published_before", type=str, required=False),
            OpenApiParameter(name="q", type=str, required=False),
            OpenApiParameter(name="sort", type=str, required=False),
        ],
        responses={200: PublicListingSerializer(many=True)},
        tags=["marketplace"],
    )
    def get(self, request):
        filters = _call(parse_listing_filters, request.query_params)
        queryset = _call(search_public, filters)
        return _page(request, queryset, public_payload, PublicListingSerializer)


class PublicListingDetailView(PublicMarketplaceView):
    @extend_schema(responses={200: PublicListingSerializer}, tags=["marketplace"])
    def get(self, request, reference):
        listing = _call(public_listing, reference)
        return Response(PublicListingSerializer(public_payload(listing)).data)


@method_decorator(csrf_protect, name="dispatch")
class PublicEnquiryCreateView(APIView):
    """DRF marks handlers CSRF-exempt. This public write opts back in."""

    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [EnquiryCreationThrottle, EnquiryAddressThrottle]

    @extend_schema(
        request=PublicEnquiryWriteSerializer,
        responses={201: PublicEnquirySerializer},
        tags=["marketplace"],
    )
    def post(self, request, reference):
        enquiry = _call(
            create_public_enquiry,
            reference=reference,
            fields=request.data,
            request=request,
        )
        body = {"id": enquiry.id, "status": enquiry.status}
        return Response(PublicEnquirySerializer(body).data, status=201)


class EnquiryCollectionView(PublisherView):
    """Read-only inbox. Scope comes from the authorized queryset."""

    http_method_names = ["get", "head", "options"]

    @extend_schema(
        parameters=[
            OpenApiParameter(name="status", type=str, required=False),
            OpenApiParameter(name="listing_reference", type=str, required=False),
            OpenApiParameter(name="created_after", type=str, required=False),
            OpenApiParameter(name="created_before", type=str, required=False),
        ],
        responses={200: EnquiryInboxSerializer(many=True)},
        tags=["marketplace"],
    )
    def get(self, request):
        filters = _call(parse_enquiry_filters, request.query_params)
        queryset = _call(list_enquiries, request.user, filters)
        return _page(request, queryset, enquiry_inbox, EnquiryInboxSerializer)


class EnquiryDetailView(PublisherView):
    """One enquiry inside the caller's authorized queryset."""

    http_method_names = ["get", "head", "options"]

    @extend_schema(responses={200: EnquiryInboxSerializer}, tags=["marketplace"])
    def get(self, request, enquiry_id):
        enquiry = _call(get_authorized_enquiry, request.user, enquiry_id)
        return Response(EnquiryInboxSerializer(enquiry_inbox(enquiry)).data)


class EnquiryAssignmentView(PublisherView):
    """Assign or reassign an enquiry to a team member, or unassign."""

    http_method_names = ["patch", "head", "options"]

    @extend_schema(
        request=EnquiryAssignmentUpdateSerializer,
        responses={200: EnquiryInboxSerializer},
        tags=["marketplace"],
    )
    def patch(self, request, enquiry_id):
        payload = _body(EnquiryAssignmentUpdateSerializer, request.data)
        enquiry = _call(
            assign_enquiry,
            user=request.user,
            enquiry_id=enquiry_id,
            user_id=payload.get("user_id"),
            request=request,
        )
        return Response(EnquiryInboxSerializer(enquiry_inbox(enquiry)).data)


class EnquiryTransitionView(PublisherView):
    """Advance an enquiry through its lifecycle graph."""

    http_method_names = ["post", "head", "options"]

    @extend_schema(
        request=EnquiryTransitionWriteSerializer,
        responses={200: EnquiryInboxSerializer},
        tags=["marketplace"],
    )
    def post(self, request, enquiry_id):
        payload = _body(EnquiryTransitionWriteSerializer, request.data)
        enquiry = _call(
            transition_enquiry,
            user=request.user,
            enquiry_id=enquiry_id,
            new_status=payload["status"],
            reason_code=payload.get("reason_code"),
            request=request,
        )
        return Response(EnquiryInboxSerializer(enquiry_inbox(enquiry)).data)


class PublisherListingCollectionView(PublisherView):
    @extend_schema(responses={200: PublisherListingSerializer(many=True)}, tags=["marketplace"])
    def get(self, request):
        queryset = _call(publisher_listings, request.user)
        return _page(request, queryset, publisher_listing, PublisherListingSerializer)

    @extend_schema(
        request=ListingWriteSerializer,
        responses={201: PublisherListingSerializer},
        tags=["marketplace"],
    )
    def post(self, request):
        payload = _body(ListingWriteSerializer, request.data, context={"creating": True})
        listing = _call(create_listing, actor=request.user, data=payload, request=request)
        return Response(PublisherListingSerializer(publisher_listing(listing)).data, status=201)


class PublisherListingDetailView(PublisherView):
    @extend_schema(responses={200: PublisherListingSerializer}, tags=["marketplace"])
    def get(self, request, listing_id):
        listing = _call(load_managed, request.user, listing_id)
        return Response(PublisherListingSerializer(publisher_listing(listing)).data)

    @extend_schema(
        request=ListingWriteSerializer,
        responses={200: PublisherListingSerializer},
        tags=["marketplace"],
    )
    def patch(self, request, listing_id):
        payload = _body(ListingWriteSerializer, request.data)
        listing = _call(
            update_listing,
            actor=request.user,
            listing_id=listing_id,
            data=payload,
            request=request,
        )
        return Response(PublisherListingSerializer(publisher_listing(listing)).data)


class _ListingActionView(PublisherView):
    operation = None

    @extend_schema(request=None, responses={200: PublisherListingSerializer}, tags=["marketplace"])
    def post(self, request, listing_id):
        listing = _call(self.operation, actor=request.user, listing_id=listing_id, request=request)
        return Response(PublisherListingSerializer(publisher_listing(listing)).data)


class SubmitListingView(_ListingActionView):
    operation = staticmethod(submit_listing)


class ReviseListingView(_ListingActionView):
    operation = staticmethod(revise_listing)


class PauseListingView(_ListingActionView):
    operation = staticmethod(pause_listing)


class ResumeListingView(_ListingActionView):
    operation = staticmethod(resume_listing)


class WithdrawListingView(_ListingActionView):
    operation = staticmethod(withdraw_listing)


class CompleteListingView(_ListingActionView):
    operation = staticmethod(complete_listing)


class ExpireListingView(_ListingActionView):
    operation = staticmethod(expire_listing)


class RenewListingView(_ListingActionView):
    operation = staticmethod(renew_listing)

    @extend_schema(request=None, responses={201: PublisherListingSerializer}, tags=["marketplace"])
    def post(self, request, listing_id):
        listing = _call(self.operation, actor=request.user, listing_id=listing_id, request=request)
        return Response(PublisherListingSerializer(publisher_listing(listing)).data, status=201)


class AcceptDescriptionView(_ListingActionView):
    operation = staticmethod(accept_ai_description)


class ListingMediaCollectionView(PublisherView):
    @extend_schema(
        request=ListingMediaWriteSerializer,
        responses={201: PublisherListingSerializer},
        tags=["marketplace"],
    )
    def post(self, request, listing_id):
        payload = _body(ListingMediaWriteSerializer, request.data)
        listing = _call(
            attach_media,
            actor=request.user,
            listing_id=listing_id,
            data=payload,
            request=request,
        )
        return Response(PublisherListingSerializer(publisher_listing(listing)).data, status=201)


class ListingMediaDetailView(PublisherView):
    @extend_schema(responses={200: PublisherListingSerializer}, tags=["marketplace"])
    def delete(self, request, listing_id, link_id):
        listing = _call(
            detach_media,
            actor=request.user,
            listing_id=listing_id,
            link_id=link_id,
            request=request,
        )
        return Response(PublisherListingSerializer(publisher_listing(listing)).data)


class MandateCollectionView(PublisherView):
    @extend_schema(responses={200: MandateSerializer(many=True)}, tags=["marketplace"])
    def get(self, request):
        queryset = _call(list_mandates, request.user)
        return _page(request, queryset, mandate_payload, MandateSerializer)

    @extend_schema(
        request=MandateWriteSerializer,
        responses={201: MandateSerializer},
        tags=["marketplace"],
    )
    def post(self, request):
        payload = _body(MandateWriteSerializer, request.data)
        mandate = _call(create_mandate, actor=request.user, data=payload, request=request)
        return Response(MandateSerializer(mandate_payload(mandate)).data, status=201)


class MandateDetailView(PublisherView):
    @extend_schema(responses={200: MandateSerializer}, tags=["marketplace"])
    def get(self, request, mandate_id):
        mandate = _call(get_mandate_for_actor, actor=request.user, mandate_id=mandate_id)
        return Response(MandateSerializer(mandate_payload(mandate)).data)


class _MandateActionView(PublisherView):
    operation = None

    @extend_schema(request=None, responses={200: MandateSerializer}, tags=["marketplace"])
    def post(self, request, mandate_id):
        mandate = _call(self.operation, actor=request.user, mandate_id=mandate_id, request=request)
        return Response(MandateSerializer(mandate_payload(mandate)).data)


class AcceptMandateView(_MandateActionView):
    operation = staticmethod(accept_mandate)


class RejectMandateView(_MandateActionView):
    operation = staticmethod(reject_mandate)


class RevokeMandateView(_MandateActionView):
    operation = staticmethod(revoke_mandate)


class PropertyMediaCollectionView(PublisherView):
    @extend_schema(responses={200: PropertyMediaSerializer(many=True)}, tags=["marketplace"])
    def get(self, request, property_id):
        queryset = _call(list_property_media, actor=request.user, property_id=property_id)
        return _page(request, queryset, property_media_payload, PropertyMediaSerializer)


class UploadSessionCreateView(PublisherView):
    @extend_schema(
        request=UploadSessionWriteSerializer,
        responses={201: UploadSessionSerializer},
        tags=["marketplace"],
    )
    def post(self, request, property_id):
        _call(reject_client_storage_claims, request.data)
        payload = _body(UploadSessionWriteSerializer, request.data)
        session = _call(
            create_upload,
            actor=request.user,
            property_id=property_id,
            data=payload,
            request=request,
        )
        return Response(UploadSessionSerializer(session).data, status=201)


class UploadSessionCompleteView(PublisherView):
    @extend_schema(request=None, responses={200: PropertyMediaSerializer}, tags=["marketplace"])
    def post(self, request, session_id):
        media = _call(complete_upload, actor=request.user, session_id=session_id, request=request)
        return Response(PropertyMediaSerializer(property_media_payload(media)).data)


class UploadSessionCancelView(PublisherView):
    @extend_schema(request=None, responses={200: PropertyMediaSerializer}, tags=["marketplace"])
    def post(self, request, session_id):
        media = _call(cancel_upload, actor=request.user, session_id=session_id, request=request)
        return Response(PropertyMediaSerializer(property_media_payload(media)).data)


class PropertyMediaDownloadView(PublisherView):
    @extend_schema(request=None, responses={200: SignedUrlSerializer}, tags=["marketplace"])
    def post(self, request, property_id, media_id):
        payload = _call(
            download_property_media,
            actor=request.user,
            property_id=property_id,
            media_id=media_id,
            request=request,
        )
        return Response(SignedUrlSerializer(payload).data)


class PublicListingPhotoView(PublicMarketplaceView):
    @extend_schema(responses={200: SignedUrlSerializer}, tags=["marketplace"])
    def get(self, request, reference, link_id):
        payload = _call(public_listing_photo, reference=reference, link_id=link_id)
        return Response(SignedUrlSerializer(payload).data)


class PropertyMediaRemoveView(PublisherView):
    @extend_schema(request=None, responses={200: PropertyMediaSerializer}, tags=["marketplace"])
    def post(self, request, property_id, media_id):
        media = _call(
            remove_property_media,
            actor=request.user,
            property_id=property_id,
            media_id=media_id,
            request=request,
        )
        return Response(PropertyMediaSerializer(property_media_payload(media)).data)


class ModerationView(PublisherView):
    def _listing(self, listing):
        return Response(ModerationListingSerializer(moderation_listing(listing)).data)


class ApproveListingView(ModerationView):
    @extend_schema(request=None, responses={200: ModerationListingSerializer}, tags=["marketplace"])
    def post(self, request, listing_id):
        listing = _call(approve_listing, actor=request.user, listing_id=listing_id, request=request)
        return self._listing(listing)


class RejectModerationView(ModerationView):
    @extend_schema(
        request=ReasonSerializer,
        responses={200: ModerationListingSerializer},
        tags=["marketplace"],
    )
    def post(self, request, listing_id):
        payload = _body(ReasonSerializer, request.data)
        listing = _call(
            reject_listing,
            actor=request.user,
            listing_id=listing_id,
            data=payload,
            request=request,
        )
        return self._listing(listing)


class SuspendListingView(ModerationView):
    @extend_schema(
        request=ReasonSerializer,
        responses={200: ModerationListingSerializer},
        tags=["marketplace"],
    )
    def post(self, request, listing_id):
        payload = _body(ReasonSerializer, request.data)
        listing = _call(
            suspend_listing,
            actor=request.user,
            listing_id=listing_id,
            data=payload,
            request=request,
        )
        return self._listing(listing)


class ReinstateListingView(ModerationView):
    @extend_schema(request=None, responses={200: ModerationListingSerializer}, tags=["marketplace"])
    def post(self, request, listing_id):
        listing = _call(
            reinstate_listing,
            actor=request.user,
            listing_id=listing_id,
            request=request,
        )
        return self._listing(listing)


class ExpireDueView(ModerationView):
    @extend_schema(request=None, responses={200: ExpireDueSerializer}, tags=["marketplace"])
    def post(self, request):
        expired = _call(expire_due, actor=request.user, request=request)
        return Response(ExpireDueSerializer({"expired": expired}).data)


class ModerationDetailView(ModerationView):
    @extend_schema(responses={200: ModerationListingSerializer}, tags=["marketplace"])
    def get(self, request, listing_id):
        from apps.marketplace.services.access import ensure_moderator

        _call(ensure_moderator, request.user)
        listing = _call(load_for_staff, listing_id)
        return self._listing(listing)
