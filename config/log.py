"""Structured logging configuration."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

from config.context import request_id_var


class RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = getattr(record, "request_id", None) or request_id_var.get()
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", "-"),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def build_logging(*, level: str, json_logs: bool) -> dict[str, object]:
    formatter = "json" if json_logs else "console"
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "filters": {"request_id": {"()": "config.log.RequestIdFilter"}},
        "formatters": {
            "json": {"()": "config.log.JsonFormatter"},
            "console": {
                "format": "%(asctime)s %(levelname)s %(name)s [%(request_id)s] %(message)s",
            },
        },
        "handlers": {
            "console": {
                "class": "logging.StreamHandler",
                "filters": ["request_id"],
                "formatter": formatter,
            }
        },
        "root": {"handlers": ["console"], "level": level},
        "loggers": {
            "django": {"handlers": ["console"], "level": level, "propagate": False},
            "django.server": {"handlers": ["console"], "level": level, "propagate": False},
            "glitters": {"handlers": ["console"], "level": level, "propagate": False},
            "celery": {"handlers": ["console"], "level": level, "propagate": False},
        },
    }
