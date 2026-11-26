"""Production settings. Debug is refused and secrets must be explicit."""

from django.core.exceptions import ImproperlyConfigured

from config.log import build_logging
from config.settings.base import *  # noqa: F403
from config.settings.base import ALLOWED_HOSTS, SECRET_KEY, env

DEBUG = False
if env.bool("DJANGO_DEBUG", default=False):
    raise ImproperlyConfigured("Production refuses DJANGO_DEBUG=true.")
if SECRET_KEY.startswith("change-me") or len(SECRET_KEY) < 50:
    raise ImproperlyConfigured("DJANGO_SECRET_KEY is not acceptable for production.")
if not ALLOWED_HOSTS or "*" in ALLOWED_HOSTS:
    raise ImproperlyConfigured("Set an explicit DJANGO_ALLOWED_HOSTS value for production.")

_LOCAL_EMAIL_BACKENDS = {
    "django.core.mail.backends.console.EmailBackend",
    "django.core.mail.backends.locmem.EmailBackend",
    "django.core.mail.backends.filebased.EmailBackend",
    "django.core.mail.backends.dummy.EmailBackend",
}
_configured_email = env("DJANGO_EMAIL_BACKEND", default="")
if not _configured_email or _configured_email in _LOCAL_EMAIL_BACKENDS:
    raise ImproperlyConfigured(
        "Set DJANGO_EMAIL_BACKEND to an external mail backend. "
        "Console and local email backends are refused because they can leak reset tokens."
    )
EMAIL_BACKEND = _configured_email

# Credentials are checked when a request needs the bucket, not at import.
MEDIA_STORAGE_BACKEND = env("MEDIA_STORAGE_BACKEND", default="r2")

SECURE_SSL_REDIRECT = env.bool("DJANGO_SECURE_SSL_REDIRECT", default=True)
SECURE_HSTS_SECONDS = 31_536_000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
LOGGING = build_logging(
    level=env("DJANGO_LOG_LEVEL", default="INFO"),
    json_logs=env.bool("DJANGO_JSON_LOGS", default=True),
)
