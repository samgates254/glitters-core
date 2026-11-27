"""Compute jobs. Postgres remains the record. The broker only wakes the worker."""

from __future__ import annotations

from celery import shared_task


@shared_task(name="spatial.run_compute_job")
def run_compute_job(job_id: str) -> str:
    """Run one job inside the trusted application. No rental credential is attached."""

    from apps.spatial.services.compute.runner import execute_job

    return execute_job(job_id)
