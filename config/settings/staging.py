"""Staging settings. Staging must not share development secrets or databases."""

from django.core.exceptions import ImproperlyConfigured

from config.log import build_logging
from config.settings.base import *  # noqa: F403
from config.settings.base import ALLOWED_HOSTS, SECRET_KEY, env

DEBUG = False
if env.bool("DJANGO_DEBUG", default=False):
    raise ImproperlyConfigured("Staging refuses DJANGO_DEBUG=true.")
if SECRET_KEY.startswith("change-me") or len(SECRET_KEY) < 50:
    raise ImproperlyConfigured("DJANGO_SECRET_KEY is not acceptable for staging.")
if not ALLOWED_HOSTS or "*" in ALLOWED_HOSTS:
    raise ImproperlyConfigured("Set an explicit DJANGO_ALLOWED_HOSTS value for staging.")

# Credentials are checked when a request needs the bucket, not at import.
MEDIA_STORAGE_BACKEND = env("MEDIA_STORAGE_BACKEND", default="r2")

SECURE_SSL_REDIRECT = env.bool("DJANGO_SECURE_SSL_REDIRECT", default=True)
SECURE_HSTS_SECONDS = 86_400
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = False
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
LOGGING = build_logging(
    level=env("DJANGO_LOG_LEVEL", default="INFO"),
    json_logs=env.bool("DJANGO_JSON_LOGS", default=True),
)
