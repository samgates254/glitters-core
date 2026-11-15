"""Celery application for GLITTERS CORE.

No business tasks are registered yet. The worker and beat processes can boot
and reach Redis. Redis is the broker, not the record of financial or booking truth.
"""

from __future__ import annotations

import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.development")

app = Celery("glitters")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
