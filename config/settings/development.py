"""Local development settings."""

from config.settings.base import *  # noqa: F403
from config.settings.base import ALLOWED_HOSTS, SPECTACULAR_SETTINGS, env

DEBUG = env.bool("DJANGO_DEBUG", default=True)
if "testserver" not in ALLOWED_HOSTS:
    ALLOWED_HOSTS = [*ALLOWED_HOSTS, "testserver"]

EMAIL_BACKEND = env(
    "DJANGO_EMAIL_BACKEND",
    default="django.core.mail.backends.console.EmailBackend",
)

SPECTACULAR_SETTINGS = {
    **SPECTACULAR_SETTINGS,
    "SERVERS": [{"url": "http://127.0.0.1:8010", "description": "Local development"}],
}
