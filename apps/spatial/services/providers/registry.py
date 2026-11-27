"""Persist the provider catalogue without treating a ping as a licence change."""

from __future__ import annotations

from django.db import transaction
from django.utils import timezone

from apps.spatial.domain import ProviderOperationalState
from apps.spatial.models import GeospatialDataSource, ProviderQueryRecord
from apps.spatial.services.providers.catalog import PROVIDERS, provider_by_id


def sync_provider_catalog() -> list[GeospatialDataSource]:
    rows: list[GeospatialDataSource] = []
    with transaction.atomic():
        for spec in PROVIDERS:
            row, _created = GeospatialDataSource.objects.update_or_create(
                provider_id=spec.provider_id,
                defaults=spec.as_row(),
            )
            rows.append(row)
    return rows


def get_provider(provider_id: str) -> GeospatialDataSource:
    sync_provider_catalog()
    try:
        return GeospatialDataSource.objects.get(provider_id=provider_id)
    except GeospatialDataSource.DoesNotExist as exc:
        raise LookupError(provider_id) from exc


def mark_unchecked(row: GeospatialDataSource, *, state: str, error: str) -> GeospatialDataSource:
    now = timezone.now()
    row.operational_state = state
    row.last_checked_at = now
    row.last_http_status = None
    row.last_error = error[:300]
    row.save(
        update_fields=[
            "operational_state",
            "last_checked_at",
            "last_http_status",
            "last_error",
            "updated_at",
        ]
    )
    return row


def mark_checked(
    row: GeospatialDataSource,
    *,
    http_status: int | None,
    state: str,
    error: str = "",
    summary: dict | None = None,
) -> GeospatialDataSource:
    now = timezone.now()
    row.last_checked_at = now
    row.last_http_status = http_status
    row.operational_state = state
    row.last_error = error[:300]
    if state == ProviderOperationalState.REACHABLE:
        row.last_success_at = now
        if summary is not None:
            row.discovered_summary = summary
    row.save(
        update_fields=[
            "last_checked_at",
            "last_success_at",
            "last_http_status",
            "operational_state",
            "last_error",
            "discovered_summary",
            "updated_at",
        ]
    )
    return row


def record_query(
    *,
    provider: GeospatialDataSource,
    organization,
    user,
    query_kind: str,
    http_status: int | None,
    operational_state: str,
    item_count: int = 0,
    collection_id: str = "",
    bbox=None,
    error: str = "",
) -> ProviderQueryRecord | None:
    if organization is None or user is None:
        return None
    return ProviderQueryRecord.objects.create(
        organization=organization,
        created_by=user,
        provider=provider,
        query_kind=query_kind,
        http_status=http_status,
        operational_state=operational_state,
        item_count=item_count,
        collection_id=collection_id[:120],
        bbox=bbox,
        error=error[:300],
    )


def spec_or_raise(provider_id: str):
    spec = provider_by_id(provider_id)
    if spec is None:
        raise LookupError(provider_id)
    return spec
