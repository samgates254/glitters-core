import os
import subprocess
import sys
from pathlib import Path

import pytest
from django.conf import settings
from django.test import override_settings
from rest_framework.test import APIClient
from tests.identity_support import enable_confirmed_mfa

from apps.identity.models import User

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PASSWORD = "Night-Bridge-Kestrel-42"


def test_cors_is_explicit_and_api_defaults_to_authenticated() -> None:
    assert settings.CORS_ALLOW_ALL_ORIGINS is False
    assert settings.REST_FRAMEWORK["DEFAULT_PERMISSION_CLASSES"] == [
        "rest_framework.permissions.IsAuthenticated",
    ]
    assert settings.CELERY_ACCEPT_CONTENT == ["json"]


@override_settings(DEBUG=True)
def test_schema_is_public_while_debug_is_on(api_client: APIClient) -> None:
    response = api_client.get("/api/v1/schema/?format=json")

    assert response.status_code == 200
    encoded = response.content.decode()
    assert "/api/v1/health/live/" in encoded
    assert "/api/v1/health/ready/" in encoded
    assert "/api/v1/auth/login/" in encoded
    assert "/api/v1/auth/register/" in encoded


@pytest.mark.django_db
@override_settings(DEBUG=False)
def test_schema_requires_a_staff_session_when_debug_is_off(api_client: APIClient) -> None:
    anonymous = api_client.get("/api/v1/schema/?format=json")
    assert anonymous.status_code in {401, 403}

    staff = User.objects.create_user(
        email="staff@example.com",
        password=PASSWORD,
        is_staff=True,
    )
    enable_confirmed_mfa(staff)
    api_client.force_login(staff)
    allowed = api_client.get("/api/v1/schema/?format=json")
    assert allowed.status_code == 200


@pytest.mark.django_db
def test_admin_requires_authentication(api_client: APIClient) -> None:
    response = api_client.get("/admin/")

    assert response.status_code == 302
    assert "/admin/login/" in response.url


def _setup_settings(module: str, **overrides: str) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment.update(
        {
            "DJANGO_SETTINGS_MODULE": module,
            "DJANGO_SECRET_KEY": "p" * 64,
            "DJANGO_DEBUG": "false",
            "DJANGO_ALLOWED_HOSTS": "api.glitters.example",
            "DATABASE_URL": "postgres://glitters:glitters-dev-only@127.0.0.1:5434/glitters",
            "REDIS_URL": "redis://127.0.0.1:6381/0",
            "CELERY_BROKER_URL": "redis://127.0.0.1:6381/1",
            "CELERY_RESULT_BACKEND": "redis://127.0.0.1:6381/2",
            "CORS_ALLOWED_ORIGINS": "https://app.glitters.example",
            "CSRF_TRUSTED_ORIGINS": "https://app.glitters.example",
        }
    )
    environment.update(overrides)
    return subprocess.run(
        [sys.executable, "-c", "import django; django.setup()"],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize("module", ["config.settings.production", "config.settings.staging"])
def test_deployed_settings_refuse_debug_and_accept_an_explicit_environment(module: str) -> None:
    rejected = _setup_settings(module, DJANGO_DEBUG="true")
    assert rejected.returncode != 0
    assert "DJANGO_DEBUG" in f"{rejected.stdout}\n{rejected.stderr}"

    rejected_secret = _setup_settings(module, DJANGO_SECRET_KEY="change-me-" + ("x" * 50))
    assert rejected_secret.returncode != 0
    assert "DJANGO_SECRET_KEY" in f"{rejected_secret.stdout}\n{rejected_secret.stderr}"

    rejected_hosts = _setup_settings(module, DJANGO_ALLOWED_HOSTS="*")
    assert rejected_hosts.returncode != 0
    assert "DJANGO_ALLOWED_HOSTS" in f"{rejected_hosts.stdout}\n{rejected_hosts.stderr}"

    accepted = _setup_settings(module)
    assert accepted.returncode == 0, accepted.stderr
