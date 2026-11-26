"""Celery application for GLITTERS CORE.

The tenancy reservation sweep is registered. Redis is the broker, not the
record of a lease, a payment, or a booking. Lease truth stays in Postgres.
"""

from __future__ import annotations

import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.development")

app = Celery("glitters")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
