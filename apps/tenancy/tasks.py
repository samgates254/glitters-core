"""Background lease maintenance. Postgres remains the source of truth."""

from __future__ import annotations

from celery import shared_task


@shared_task(name="tenancy.expire_due_reservations")
def expire_due_reservations_task() -> int:
    """Expire pending-signature reservations whose deadline has passed.

    Repeated runs do not write a second audit event for the same reservation.
    """

    from apps.tenancy.services.leases import expire_due_reservations

    return expire_due_reservations(trusted_system=True)
