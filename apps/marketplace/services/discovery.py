"""Structured discovery on PostgreSQL. There is no separate search service."""

from __future__ import annotations

from uuid import UUID

from django.contrib.postgres.search import SearchQuery
from django.db.models import BigIntegerField, Exists, F, OuterRef, Prefetch, Q, Subquery
from django.utils import timezone

from apps.identity.domain import AccountStatus
from apps.locations.models import AdministrativeArea
from apps.marketplace.domain import (
    PUBLIC_ATTRIBUTE_CODES,
    BillingPeriod,
    ListingStatus,
    ListingType,
    MandateStatus,
    PricePresentation,
    verified_photo_filters,
)
from apps.marketplace.filters import ListingFilters
from apps.marketplace.models import Listing, ListingMedia, ListingPrice
from apps.organizations.domain import MembershipStatus, OrganizationStatus, Permission
from apps.organizations.models import OrganizationMembership
from apps.properties.domain import PropertyStatus
from apps.properties.models import PropertyAttribute

_SALE = (ListingType.SALE, ListingType.LAND_SALE, ListingType.DEVELOPMENT)
_MONTHLY = (ListingType.LONG_TERM_RENTAL, ListingType.COMMERCIAL_LEASE)
_CHAIN_DEPTH = 8


def eligible_listings():
    """Published listings that still meet the public rules. This does not write."""

    today = timezone.localdate()
    now = timezone.now()
    photo = ListingMedia.objects.filter(listing_id=OuterRef("pk"), **verified_photo_filters())
    price = ListingPrice.objects.filter(listing_id=OuterRef("pk"), superseded_at__isnull=True)
    membership = OrganizationMembership.objects.filter(
        organization_id=OuterRef("advertising_organization_id"),
        user_id=OuterRef("advertiser_id"),
        status=MembershipStatus.ACTIVE,
        role__in=_publish_roles(),
    )
    return prepared(
        Listing.objects.filter(
            status=ListingStatus.PUBLISHED,
            moderation_hold=False,
            expires_at__gt=now,
            property__status=PropertyStatus.ACTIVE,
            mandate__status=MandateStatus.ACTIVE,
            mandate__valid_from__lte=today,
            advertiser__status=AccountStatus.ACTIVE,
            advertiser__email_verified_at__isnull=False,
        )
        .filter(Q(mandate__valid_until__isnull=True) | Q(mandate__valid_until__gte=today))
        .filter(
            Q(advertising_organization__isnull=True)
            | (Q(advertising_organization__status=OrganizationStatus.ACTIVE) & Exists(membership))
        )
        .filter(Exists(photo))
        .filter(
            (Q(listing_type__in=_SALE) & Exists(price.filter(billing_period=BillingPeriod.SALE)))
            | (
                Q(listing_type__in=_MONTHLY)
                & Exists(price.filter(billing_period__in=[BillingPeriod.MONTH, BillingPeriod.YEAR]))
            )
            | (
                Q(listing_type=ListingType.SHORT_TERM)
                & Exists(price.filter(billing_period__in=[BillingPeriod.NIGHT, BillingPeriod.WEEK]))
            )
        )
    )


def prepared(queryset):
    return (
        queryset.select_related(
            "property",
            "property__location",
            "property__location__address",
            "property__location__address__administrative_area",
            "property__location__address__locality",
            "advertiser",
            "advertising_organization",
            "mandate",
        )
        .defer("property__location__coordinates")
        .prefetch_related(
            Prefetch(
                "property__attributes",
                queryset=PropertyAttribute.objects.filter(code__in=PUBLIC_ATTRIBUTE_CODES),
            ),
            Prefetch(
                "prices",
                queryset=ListingPrice.objects.filter(superseded_at__isnull=True),
            ),
            Prefetch(
                "listing_media",
                queryset=ListingMedia.objects.select_related("property_media")
                .filter(**verified_photo_filters())
                .order_by("sort_order", "id"),
            ),
        )
    )


def search_public(filters: ListingFilters):
    return _apply(eligible_listings(), filters)


def publisher_listings(user):
    from apps.marketplace.services.access import publish_roles

    personal = Q(advertising_organization__isnull=True, advertiser=user)
    org_ids = OrganizationMembership.objects.filter(
        user=user,
        status=MembershipStatus.ACTIVE,
        organization__status=OrganizationStatus.ACTIVE,
        role__in=publish_roles(),
    ).values("organization_id")
    return prepared(
        Listing.objects.filter(personal | Q(advertising_organization_id__in=org_ids))
    ).order_by("-updated_at", "-created_at")


def _apply(queryset, filters: ListingFilters):
    if filters.listing_type:
        queryset = queryset.filter(listing_type=filters.listing_type)
    if filters.property_type:
        queryset = queryset.filter(property__property_type=filters.property_type)
    if filters.country:
        queryset = queryset.filter(property__location__address__country_id=filters.country)
    if filters.administrative_area_id:
        queryset = queryset.filter(
            property__location__address__administrative_area_id__in=_area_ids(
                filters.administrative_area_id
            )
        )
    if filters.locality_id:
        queryset = queryset.filter(property__location__address__locality_id=filters.locality_id)
    if filters.bedrooms is not None:
        queryset = queryset.filter(_attribute_at_least("bedrooms", filters.bedrooms))
    if filters.bathrooms is not None:
        queryset = queryset.filter(_attribute_at_least("bathrooms", filters.bathrooms))
    if filters.floor_area_min is not None:
        queryset = queryset.filter(_area_at_least("floor_area_sqm", filters.floor_area_min))
    if filters.land_area_min is not None:
        queryset = queryset.filter(_area_at_least("plot_area_sqm", filters.land_area_min))
    if filters.published_after is not None:
        queryset = queryset.filter(published_at__gte=filters.published_after)
    if filters.published_before is not None:
        queryset = queryset.filter(published_at__lte=filters.published_before)
    if filters.currency or filters.price_min is not None or filters.price_max is not None:
        queryset = queryset.filter(_price_filter(filters))
    if filters.query:
        queryset = queryset.filter(search_vector=SearchQuery(filters.query, config="simple"))
    return _sort(queryset, filters.sort)


def _price_filter(filters: ListingFilters):
    price = ListingPrice.objects.filter(listing_id=OuterRef("pk"), superseded_at__isnull=True)
    if filters.currency:
        price = price.filter(currency=filters.currency)
    if filters.price_min is not None or filters.price_max is not None:
        price = price.filter(presentation=PricePresentation.ASKING)
        if filters.price_min is not None:
            price = price.filter(amount_minor__gte=filters.price_min)
        if filters.price_max is not None:
            price = price.filter(amount_minor__lte=filters.price_max)
    return Exists(price)


def _attribute_at_least(code: str, minimum: int):
    return Exists(
        PropertyAttribute.objects.filter(
            property_id=OuterRef("property_id"),
            code=code,
            value_integer__gte=minimum,
        )
    )


def _area_at_least(code: str, minimum):
    return Exists(
        PropertyAttribute.objects.filter(
            property_id=OuterRef("property_id"),
            code=code,
            value_decimal__gte=minimum,
        )
    )


def _sort(queryset, sort: str):
    if sort in {"price", "-price"}:
        amount = ListingPrice.objects.filter(
            listing_id=OuterRef("pk"),
            superseded_at__isnull=True,
        ).values("amount_minor")[:1]
        queryset = queryset.annotate(sort_amount=Subquery(amount, output_field=BigIntegerField()))
        direction = F("sort_amount").asc(nulls_last=True)
        if sort == "-price":
            direction = F("sort_amount").desc(nulls_last=True)
        return queryset.order_by(direction, "id")
    if sort == "published_at":
        return queryset.order_by("published_at", "id")
    return queryset.order_by("-published_at", "-id")


def _area_ids(area_id: UUID) -> set[UUID]:
    if not AdministrativeArea.objects.filter(pk=area_id).exists():
        from apps.marketplace.domain import FILTER_INVALID
        from apps.marketplace.errors import MarketplaceError

        raise MarketplaceError(FILTER_INVALID, status_code=400)
    collected = {area_id}
    frontier = {area_id}
    for _depth in range(_CHAIN_DEPTH):
        children = set(
            AdministrativeArea.objects.filter(parent_id__in=frontier).values_list("id", flat=True)
        )
        children -= collected
        if not children:
            break
        collected |= children
        frontier = children
    return collected


def _publish_roles() -> list[str]:
    from apps.organizations.domain import ROLE_PERMISSIONS

    return [
        role
        for role, permissions in ROLE_PERMISSIONS.items()
        if Permission.PROPERTY_PUBLISH in permissions
    ]
