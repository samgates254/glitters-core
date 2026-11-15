"""Dependency checks used by the readiness endpoint."""

from __future__ import annotations

import logging
from typing import Any

from django.conf import settings
from django.db import connection
from redis import Redis

from config.version import __version__

logger = logging.getLogger("glitters.health")
SERVICE_NAME = "glitters-core"


def live_payload(request_id: str) -> dict[str, str]:
    return {
        "status": "ok",
        "service": SERVICE_NAME,
        "version": __version__,
        "request_id": request_id,
    }


def ready_payload(request_id: str) -> tuple[dict[str, Any], int]:
    checks = {
        "database": _check_database(),
        "postgis": _check_postgis(),
        "redis": _check_redis(),
    }
    healthy = all(item["status"] == "ok" for item in checks.values())
    payload = {
        "status": "ok" if healthy else "error",
        "service": SERVICE_NAME,
        "version": __version__,
        "request_id": request_id,
        "checks": checks,
    }
    return payload, 200 if healthy else 503


def _check_database() -> dict[str, str]:
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except Exception:
        logger.exception("database health check failed")
        return {"status": "error", "detail": "unavailable"}
    return {"status": "ok"}


def _check_postgis() -> dict[str, str]:
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT PostGIS_Version()")
            row = cursor.fetchone()
    except Exception:
        logger.exception("postgis health check failed")
        return {"status": "error", "detail": "unavailable"}
    version = row[0] if row else ""
    return {"status": "ok", "version": version}


def _check_redis() -> dict[str, str]:
    client = Redis.from_url(
        settings.REDIS_URL,
        socket_connect_timeout=1,
        socket_timeout=1,
    )
    try:
        if client.ping() is not True:
            raise RuntimeError("Redis ping returned a negative result")
    except Exception:
        logger.exception("redis health check failed")
        return {"status": "error", "detail": "unavailable"}
    finally:
        client.close()
    return {"status": "ok"}
