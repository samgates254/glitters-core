"""Settings shared by development, staging, and production."""

from __future__ import annotations

import ctypes.util
import os
from pathlib import Path

import environ

from apps.identity.domain import AUTH_THROTTLE_RATES
from config.log import build_logging
from config.version import __version__

BASE_DIR = Path(__file__).resolve().parents[2]
APP_VERSION = __version__

env = environ.Env()

# Only development reads a project .env file. Staging and production take
# their configuration from the process environment so a local .env cannot
# leak into a deployed process.
_settings_module = os.environ.get("DJANGO_SETTINGS_MODULE", "")
_env_file = BASE_DIR / ".env"
if _settings_module.endswith(".development") and _env_file.exists():
    env.read_env(_env_file, overwrite=False)

SECRET_KEY = env("DJANGO_SECRET_KEY")
DEBUG = env.bool("DJANGO_DEBUG", default=False)
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=["localhost", "127.0.0.1"])
CSRF_TRUSTED_ORIGINS = env.list("CSRF_TRUSTED_ORIGINS", default=[])


def _library_available(env_var: str, library_name: str) -> bool:
    configured = os.environ.get(env_var, "")
    if configured and Path(configured).is_file():
        return True
    return ctypes.util.find_library(library_name) is not None


GDAL_AVAILABLE = _library_available("GDAL_LIBRARY_PATH", "gdal")
_gdal_path = os.environ.get("GDAL_LIBRARY_PATH", "")
if _gdal_path:
    GDAL_LIBRARY_PATH = _gdal_path
_geos_path = os.environ.get("GEOS_LIBRARY_PATH", "")
if _geos_path:
    GEOS_LIBRARY_PATH = _geos_path

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.postgres",
    "corsheaders",
    "rest_framework",
    "drf_spectacular",
    "apps.core.apps.CoreConfig",
    "apps.identity.apps.IdentityConfig",
    "apps.organizations.apps.OrganizationsConfig",
    "apps.locations.apps.LocationsConfig",
    "apps.properties.apps.PropertiesConfig",
    "apps.marketplace.apps.MarketplaceConfig",
    "apps.solutions.apps.SolutionsConfig",
    "apps.operations.apps.OperationsConfig",
    "apps.tenancy.apps.TenancyConfig",
    "apps.spatial.apps.SpatialConfig",
]
if GDAL_AVAILABLE:
    INSTALLED_APPS.insert(6, "django.contrib.gis")

MIDDLEWARE = [
    "config.middleware.RequestIdMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "apps.identity.middleware.ActiveAccountMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    }
]

DATABASES = {"default": env.db("DATABASE_URL")}
if GDAL_AVAILABLE:
    DATABASES["default"]["ENGINE"] = "django.contrib.gis.db.backends.postgis"
else:
    DATABASES["default"]["ENGINE"] = "django.db.backends.postgresql"
DATABASES["default"]["CONN_MAX_AGE"] = env.int("DATABASE_CONN_MAX_AGE", default=60)
DATABASES["default"]["CONN_HEALTH_CHECKS"] = True
DATABASES["default"].setdefault("OPTIONS", {})
DATABASES["default"]["OPTIONS"].setdefault("connect_timeout", 5)
DATABASES["default"]["ATOMIC_REQUESTS"] = False

AUTH_USER_MODEL = "identity.User"
AUTHENTICATION_BACKENDS = ["apps.identity.backends.IdentityBackend"]
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2SHA1PasswordHasher",
    "django.contrib.auth.hashers.ScryptPasswordHasher",
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "Africa/Nairobi"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"
SPATIAL_TILESET_ROOT = env("SPATIAL_TILESET_ROOT", default=str(MEDIA_ROOT / "digital_twins"))
SPATIAL_RASTER_ROOT = env("SPATIAL_RASTER_ROOT", default=str(MEDIA_ROOT / "rasters"))
# Object bytes live in a private bucket. The default adapter keeps development
# and the test suite independent of Cloudflare credentials.
MEDIA_STORAGE_BACKEND = env("MEDIA_STORAGE_BACKEND", default="memory")
R2_ENDPOINT_URL = env("R2_ENDPOINT_URL", default="")
R2_ACCESS_KEY_ID = env("R2_ACCESS_KEY_ID", default="")
R2_SECRET_ACCESS_KEY = env("R2_SECRET_ACCESS_KEY", default="")
R2_BUCKET = env("R2_BUCKET", default="")
R2_REGION = env("R2_REGION", default="auto")
MEDIA_UPLOAD_PUT_SECONDS = env.int("MEDIA_UPLOAD_PUT_SECONDS", default=600)
MEDIA_UPLOAD_GET_SECONDS = env.int("MEDIA_UPLOAD_GET_SECONDS", default=120)
MEDIA_UPLOAD_SESSION_SECONDS = env.int("MEDIA_UPLOAD_SESSION_SECONDS", default=900)
MEDIA_MAX_IMAGE_BYTES = env.int("MEDIA_MAX_IMAGE_BYTES", default=10_485_760)
MEDIA_MAX_DOCUMENT_BYTES = env.int("MEDIA_MAX_DOCUMENT_BYTES", default=20_971_520)
MEDIA_UPLOAD_CLEANUP_BATCH = env.int("MEDIA_UPLOAD_CLEANUP_BATCH", default=25)
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

REDIS_URL = env("REDIS_URL")
CELERY_BROKER_URL = env("CELERY_BROKER_URL")
CELERY_RESULT_BACKEND = env("CELERY_RESULT_BACKEND")
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TIMEZONE = TIME_ZONE
CELERY_ENABLE_UTC = True
CELERY_TASK_ACKS_LATE = True
CELERY_TASK_REJECT_ON_WORKER_LOST = True
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERY_TASK_TRACK_STARTED = True
CELERY_TASK_TIME_LIMIT = 300
CELERY_TASK_SOFT_TIME_LIMIT = 240
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True
CELERY_BEAT_SCHEDULE: dict[str, object] = {}

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": REDIS_URL,
        "KEY_PREFIX": "glitters",
        "TIMEOUT": 300,
    }
}

SESSION_ENGINE = "django.contrib.sessions.backends.db"
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
# The CSRF cookie must be readable by a browser client so it can copy the value
# into X-CSRFToken. The session cookie stays HTTP-only.
CSRF_COOKIE_HTTPONLY = False
CSRF_COOKIE_SAMESITE = "Lax"
CSRF_FAILURE_VIEW = "apps.identity.csrf.csrf_failure"
DEFAULT_FROM_EMAIL = "GLITTERS <noreply@glitters.local>"
IDENTITY_THROTTLE = True
ENQUIRY_THROTTLE = True
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"

CORS_ALLOWED_ORIGINS = env.list("CORS_ALLOWED_ORIGINS", default=[])
CORS_ALLOW_CREDENTIALS = True
CORS_ALLOW_ALL_ORIGINS = False

EMAIL_BACKEND = env(
    "DJANGO_EMAIL_BACKEND",
    default="django.core.mail.backends.console.EmailBackend",
)

REST_FRAMEWORK = {
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "apps.identity.authentication.BearerCredentialAuthentication",
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 25,
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.AnonRateThrottle",
        "rest_framework.throttling.UserRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "anon": "60/min",
        "user": "600/min",
        **AUTH_THROTTLE_RATES,
        # Public enquiries use a dedicated pair. The address bucket stops
        # rotating-email spray. The create bucket also includes an email digest.
        "enquiry_create": "5/hour",
        "enquiry_create_address": "30/hour",
        "spatial_compute": "30/minute",
    },
    "EXCEPTION_HANDLER": "config.api.glitters_exception_handler",
}

SPECTACULAR_SETTINGS = {
    "TITLE": "GLITTERS CORE API",
    "DESCRIPTION": (
        "HTTP API for GLITTERS, the real estate platform of GLITTERS EQUITY INVESTMENT SOLUTIONS."
    ),
    "VERSION": APP_VERSION,
    "SERVE_INCLUDE_SCHEMA": False,
}

LOGGING = build_logging(
    level=env("DJANGO_LOG_LEVEL", default="INFO"),
    json_logs=env.bool("DJANGO_JSON_LOGS", default=False),
)
