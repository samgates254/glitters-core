"""Authentication abuse cases."""

import logging
import threading

import pytest
from django.core.cache import cache
from django.test import Client
from rest_framework.test import APIClient
from tests.identity_support import error_detail, latest_mail_value

from apps.identity.domain import GENERIC_REGISTER, LOGIN_FAILED
from apps.identity.models import IdentityEvent, User
from apps.identity.throttles import LoginThrottle

LOGIN = "/api/v1/auth/login/"
ME = "/api/v1/auth/me/"
VERIFY = "/api/v1/auth/email/verify/"
REGISTER = "/api/v1/auth/register/"


@pytest.fixture(autouse=True)
def _identity_mail(locmem_email):
    return locmem_email


@pytest.mark.django_db
def test_browser_login_requires_a_csrf_token(password) -> None:
    User.objects.create_user(email="ada@example.com", password=password)
    client = APIClient(enforce_csrf_checks=True)
    missing = client.post(
        LOGIN,
        {"email": "ada@example.com", "password": password},
        format="json",
    )
    assert missing.status_code == 403
    assert error_detail(missing) == "CSRF verification failed."
    assert password not in missing.content.decode()

    issued = client.get("/api/v1/auth/csrf/")
    assert issued.status_code == 200
    token = client.cookies["csrftoken"].value
    assert client.cookies["csrftoken"]["httponly"] == ""
    signed_in = client.post(
        LOGIN,
        {"email": "ada@example.com", "password": password},
        format="json",
        HTTP_X_CSRFTOKEN=token,
    )
    assert signed_in.status_code == 200
    assert client.cookies["sessionid"]["httponly"]


@pytest.mark.django_db
def test_login_is_throttled_by_address_and_email(settings, monkeypatch, password) -> None:
    settings.IDENTITY_THROTTLE = True
    monkeypatch.setattr(LoginThrottle, "get_rate", lambda self: "2/min")
    cache.clear()
    User.objects.create_user(email="ada@example.com", password=password)
    client = APIClient()
    body = {"email": "Ada@Example.com", "password": "not-the-password"}
    statuses = [
        client.post(LOGIN, body, format="json").status_code,
        client.post(LOGIN, body, format="json").status_code,
        client.post(LOGIN, body, format="json").status_code,
    ]
    assert statuses == [401, 401, 429]

    other_email = client.post(
        LOGIN,
        {"email": "bea@example.com", "password": "not-the-password"},
        format="json",
    )
    assert other_email.status_code == 401
    assert error_detail(other_email) == LOGIN_FAILED


@pytest.mark.django_db
def test_cross_user_bearer_cannot_read_or_edit_another_profile(password) -> None:
    ada = User.objects.create_user(email="ada@example.com", password=password, first_name="Ada")
    bea = User.objects.create_user(email="bea@example.com", password=password, first_name="Bea")
    ada_client = APIClient()
    bea_client = APIClient()
    ada_token = ada_client.post(
        LOGIN,
        {"email": ada.email, "password": password, "client": "mobile"},
        format="json",
    ).json()["access_token"]
    bea_token = bea_client.post(
        LOGIN,
        {"email": bea.email, "password": password, "client": "mobile"},
        format="json",
    ).json()["access_token"]

    ada_client.credentials(HTTP_AUTHORIZATION=f"Bearer {ada_token}")
    seen = ada_client.get(ME)
    assert seen.status_code == 200
    assert seen.json()["id"] == str(ada.id)
    assert seen.json()["email"] == ada.email
    edited = ada_client.patch(ME, {"first_name": "Augusta"}, format="json")
    assert edited.json()["id"] == str(ada.id)
    bea.refresh_from_db()
    assert bea.first_name == "Bea"

    bea_client.credentials(HTTP_AUTHORIZATION=f"Bearer {bea_token}")
    assert bea_client.get(ME).json()["email"] == bea.email
    ada_client.credentials(HTTP_AUTHORIZATION=f"Bearer {bea_token}")
    assert ada_client.get(ME).json()["email"] == bea.email


@pytest.mark.django_db
def test_staff_without_confirmed_mfa_cannot_use_admin_or_the_api(password) -> None:
    User.objects.create_superuser(email="ops@example.com", password=password)
    client = Client()
    assert client.login(username="ops@example.com", password=password)
    api = client.get(ME)
    assert api.status_code == 403
    assert b"Multi-factor authentication is required." in api.content
    assert client.login(username="ops@example.com", password=password)
    admin = client.get("/admin/")
    assert admin.status_code == 302
    assert "/admin/login/" in admin.url
    health = client.get("/api/v1/health/live/")
    assert health.status_code == 200


@pytest.mark.django_db
def test_failed_login_does_not_log_the_password(caplog, password) -> None:
    User.objects.create_user(email="ada@example.com", password=password)
    client = APIClient()
    with caplog.at_level(logging.DEBUG):
        response = client.post(
            LOGIN,
            {"email": "ada@example.com", "password": password + "-wrong"},
            format="json",
        )
    assert response.status_code == 401
    assert password not in caplog.text
    event = IdentityEvent.objects.get(event_type="login_failed")
    assert event.user_agent == ""
    assert password not in (event.user_agent or "")


@pytest.mark.django_db(transaction=True)
def test_repeated_email_verification_cannot_succeed_twice(password) -> None:
    client = APIClient()
    created = client.post(
        REGISTER,
        {"email": "ada@example.com", "password": password},
        format="json",
    )
    assert created.status_code == 202
    assert created.json()["detail"] == GENERIC_REGISTER
    token = latest_mail_value("token:")
    results: list[int] = []

    def attempt() -> None:
        from django.db import connection

        try:
            response = APIClient().post(VERIFY, {"token": token}, format="json")
            results.append(response.status_code)
        finally:
            connection.close()

    threads = [threading.Thread(target=attempt) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(results) == [200, 400]
    assert User.objects.get().email_verified_at is not None
