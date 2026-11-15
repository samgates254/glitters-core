"""Temporal spatial service layer for GLITTERS Core.

Implements bi-temporal query operators and state transitions:
- Valid Time (Physical reality): valid_from, valid_to
- Transaction Time (System truth): created_at, superseded_at
"""

from __future__ import annotations

from datetime import datetime
import logging
from typing import Optional, Union

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone

from apps.spatial.models import SpatialEntity

logger = logging.getLogger(__name__)


def parse_iso_datetime(value: Union[str, datetime]) -> datetime:
    """
    Parses an ISO-8601 string or returns timezone-aware datetime.
    Raises ValidationError on invalid or unparseable input.
    """
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        try:
            # Replace 'Z' with '+00:00' for Python fromisoformat compatibility
            clean_str = value.strip().replace("Z", "+00:00")
            # Handle unencoded '+' converted to space in URL query params (e.g. " 00:00")
            if " " in clean_str and ("+" not in clean_str and "-" not in clean_str[-6:]):
                parts = clean_str.rsplit(" ", 1)
                if len(parts) == 2 and ":" in parts[1]:
                    clean_str = f"{parts[0]}+{parts[1]}"
            dt = datetime.fromisoformat(clean_str)
        except (ValueError, TypeError) as e:
            raise ValidationError(
                f"Invalid ISO-8601 datetime format '{value}'. Expected format: YYYY-MM-DDTHH:MM:SSZ."
            ) from e
    else:
        raise ValidationError(f"Expected datetime or ISO string, received: {type(value)}")

    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt, timezone.utc)
    return dt


def get_current_record(queryset: models.QuerySet) -> models.QuerySet:
    """
    Filters for current active records:
    - Never superseded in transaction time: superseded_at IS NULL
    - Currently active in valid time: valid_to IS NULL OR valid_to > now
    """
    now = timezone.now()
    return queryset.filter(
        superseded_at__isnull=True
    ).filter(
        models.Q(valid_to__isnull=True) | models.Q(valid_to__gt=now)
    )


def get_as_of(queryset: models.QuerySet, as_of_time: Union[str, datetime]) -> models.QuerySet:
    """
    Valid-Time Slice (AS OF):
    Returns entities that physically existed in reality at the specified time,
    according to current active system knowledge.

    Criteria:
    - valid_from <= as_of_time
    - valid_to IS NULL OR valid_to > as_of_time
    - superseded_at IS NULL (only current assertions)
    """
    target_dt = parse_iso_datetime(as_of_time)
    return queryset.filter(
        valid_from__lte=target_dt,
        superseded_at__isnull=True,
    ).filter(
        models.Q(valid_to__isnull=True) | models.Q(valid_to__gt=target_dt)
    )


def get_as_was(queryset: models.QuerySet, as_was_time: Union[str, datetime]) -> models.QuerySet:
    """
    Transaction-Time Slice (AS WAS):
    Returns what the database asserted as recorded truth at historical transaction time.

    Criteria:
    - created_at <= as_was_time (entity was created on or before that system time)
    - superseded_at IS NULL OR superseded_at > as_was_time (entity had not yet been superseded)
    """
    target_dt = parse_iso_datetime(as_was_time)
    return queryset.filter(
        created_at__lte=target_dt,
    ).filter(
        models.Q(superseded_at__isnull=True) | models.Q(superseded_at__gt=target_dt)
    )


def get_bi_temporal_slice(
    queryset: models.QuerySet,
    as_of_time: Optional[Union[str, datetime]] = None,
    as_was_time: Optional[Union[str, datetime]] = None,
) -> models.QuerySet:
    """
    Applies both valid-time (AS OF) and transaction-time (AS WAS) filters orthogonally.
    If neither parameter is passed, returns active current records.
    """
    qs = queryset
    if as_was_time:
        qs = get_as_was(qs, as_was_time)
        if as_of_time:
            # At historical transaction time, what did we believe physically existed at as_of_time?
            target_of = parse_iso_datetime(as_of_time)
            qs = qs.filter(
                valid_from__lte=target_of
            ).filter(
                models.Q(valid_to__isnull=True) | models.Q(valid_to__gt=target_of)
            )
    elif as_of_time:
        qs = get_as_of(qs, as_of_time)
    else:
        qs = get_current_record(qs)

    return qs


@transaction.atomic
def supersede_record(
    existing_entity: SpatialEntity,
    new_entity: SpatialEntity,
    effective_time: Optional[datetime] = None,
) -> Tuple[SpatialEntity, SpatialEntity]:
    """
    Atomically supersedes an existing spatial entity with a new successor entity.

    Invariants:
    1. An already superseded entity cannot be superseded again.
    2. The existing entity receives superseded_at and superseded_by = new_entity.
    3. The existing entity's valid_to is closed if not already set.
    """
    if existing_entity.superseded_at is not None:
        raise ValidationError(
            f"Entity {existing_entity.canonical_urn} is already superseded at {existing_entity.superseded_at}."
        )

    now = effective_time or timezone.now()

    # Lock existing entity for update
    locked_existing = SpatialEntity.objects.select_for_update().get(id=existing_entity.id)
    if locked_existing.superseded_at is not None:
        raise ValidationError(
            f"Concurrent modification: Entity {locked_existing.canonical_urn} was already superseded."
        )

    locked_existing.superseded_at = now
    locked_existing.superseded_by = new_entity

    # Close valid_to interval if open
    if locked_existing.valid_to is None or locked_existing.valid_to > now:
        locked_existing.valid_to = now

    locked_existing.save(update_fields=["superseded_at", "superseded_by", "valid_to", "updated_at"])

    return locked_existing, new_entity


def validate_temporal_transition(
    existing_entity: SpatialEntity,
    new_valid_from: datetime,
    new_valid_to: Optional[datetime] = None,
) -> None:
    """
    Validates physical time consistency for a successor entity.
    """
    if new_valid_to and new_valid_to <= new_valid_from:
        raise ValidationError(
            f"valid_to ({new_valid_to}) must be strictly greater than valid_from ({new_valid_from})."
        )
    if existing_entity.valid_from and new_valid_from < existing_entity.valid_from:
        raise ValidationError(
            f"Successor valid_from ({new_valid_from}) cannot precede predecessor valid_from ({existing_entity.valid_from})."
        )
