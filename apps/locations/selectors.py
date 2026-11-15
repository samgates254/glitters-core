"""Read queries for countries, administrative areas, and localities."""

from __future__ import annotations

from uuid import UUID

from django.db import connection
from django.db.models import CharField, F, Q, Value

from apps.locations.domain import LOCATION_NOT_FOUND, SEARCH_TOO_SHORT, normalize_country
from apps.locations.errors import LocationError
from apps.locations.models import AdministrativeArea, AdministrativeLevel, Country, Locality


def countries():
    return Country.objects.filter(active=True).order_by("name")


def country_or_error(iso: str) -> Country:
    try:
        code = normalize_country(iso)
    except ValueError as exc:
        raise LocationError(LOCATION_NOT_FOUND, status_code=404) from exc
    try:
        return Country.objects.get(pk=code, active=True)
    except Country.DoesNotExist as exc:
        raise LocationError(LOCATION_NOT_FOUND, status_code=404) from exc


def areas_for_country(country: Country, *, parent_id=None, level_code: str | None = None):
    queryset = AdministrativeArea.objects.select_related("level", "parent").filter(country=country)
    if parent_id not in (None, ""):
        parent = _area_in_country(country, parent_id)
        queryset = queryset.filter(parent=parent)
    else:
        queryset = queryset.filter(parent__isnull=True)
    if level_code:
        queryset = queryset.filter(level__code=str(level_code).strip().lower())
    return queryset.order_by("name")


def area_hierarchy(area_id):
    try:
        area_uuid = area_id if isinstance(area_id, UUID) else UUID(str(area_id))
    except (TypeError, ValueError) as exc:
        raise LocationError(LOCATION_NOT_FOUND, status_code=404) from exc
    try:
        area = AdministrativeArea.objects.select_related("level", "country", "parent").get(
            pk=area_uuid
        )
    except AdministrativeArea.DoesNotExist as exc:
        raise LocationError(LOCATION_NOT_FOUND, status_code=404) from exc
    ancestors: list[AdministrativeArea] = []
    current = area.parent
    seen = {area.id}
    while current is not None and current.id not in seen and len(ancestors) < 12:
        ancestors.append(current)
        seen.add(current.id)
        current = current.parent
    ancestors.reverse()
    children = area.children.select_related("level", "parent").order_by("name")
    levels = AdministrativeLevel.objects.filter(country=area.country).order_by("depth")
    return area, ancestors, children, levels


def search_localities(*, query: str, country: str | None = None, administrative_area_id=None):
    text = str(query or "").strip()
    if len(text) < 2:
        raise LocationError(SEARCH_TOO_SHORT, status_code=400)
    queryset = Locality.objects.select_related("administrative_area", "country").filter(
        name__icontains=text
    )
    if country:
        queryset = queryset.filter(country=country_or_error(country))
    if administrative_area_id not in (None, ""):
        area_ids = descendant_area_ids(administrative_area_id)
        if not area_ids:
            raise LocationError(LOCATION_NOT_FOUND, status_code=400)
        queryset = queryset.filter(administrative_area_id__in=area_ids)
    return queryset.order_by("name")


def search_locations(*, query: str, country: str | None = None):
    text = str(query or "").strip()
    if len(text) < 2:
        raise LocationError(SEARCH_TOO_SHORT, status_code=400)
    country_code = None
    if country:
        country_code = country_or_error(country).pk
    areas = AdministrativeArea.objects.filter(name__icontains=text)
    localities = Locality.objects.filter(name__icontains=text)
    if country_code is not None:
        areas = areas.filter(country_id=country_code)
        localities = localities.filter(country_id=country_code)
    area_rows = areas.annotate(
        result_kind=Value("administrative_area", output_field=CharField()),
        level_code=F("level__code"),
        country_code=F("country_id"),
    ).values("id", "name", "result_kind", "level_code", "parent_id", "country_code")
    locality_rows = localities.annotate(
        result_kind=Value("locality", output_field=CharField()),
        level_code=Value("", output_field=CharField()),
        country_code=F("country_id"),
        parent_id=F("administrative_area_id"),
    ).values("id", "name", "result_kind", "level_code", "parent_id", "country_code")
    return area_rows.union(locality_rows).order_by("name", "result_kind", "id")


def descendant_area_ids(area_id) -> list:
    try:
        area_uuid = area_id if isinstance(area_id, UUID) else UUID(str(area_id))
    except (TypeError, ValueError) as exc:
        raise LocationError(LOCATION_NOT_FOUND, status_code=400) from exc
    with connection.cursor() as cursor:
        cursor.execute(
            """
            WITH RECURSIVE tree AS (
                SELECT id FROM administrative_area WHERE id = %s
                UNION ALL
                SELECT child.id
                FROM administrative_area AS child
                INNER JOIN tree ON child.parent_id = tree.id
            )
            SELECT id FROM tree
            """,
            [str(area_uuid)],
        )
        return [row[0] for row in cursor.fetchall()]


def _area_in_country(country: Country, area_id) -> AdministrativeArea:
    try:
        area_uuid = area_id if isinstance(area_id, UUID) else UUID(str(area_id))
    except (TypeError, ValueError) as exc:
        raise LocationError(LOCATION_NOT_FOUND, status_code=404) from exc
    try:
        return AdministrativeArea.objects.get(pk=area_uuid, country=country)
    except AdministrativeArea.DoesNotExist as exc:
        raise LocationError(LOCATION_NOT_FOUND, status_code=404) from exc


def level_codes(country: Country) -> list[str]:
    return list(
        AdministrativeLevel.objects.filter(country=country)
        .order_by("depth")
        .values_list("code", flat=True)
    )


def areas_matching(area_id) -> Q:
    """Q fragment for callers that already resolved descendant ids."""

    return Q(pk__in=descendant_area_ids(area_id))
