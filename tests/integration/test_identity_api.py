"""Identity API flows."""

from datetime import timedelta

import pytest
from django.contrib.sessions.models import Session
from django.core import mail
from django.utils import timezone
from rest_framework.test import APIClient
from tests.identity_support import error_detail, latest_mail_value

from apps.identity.domain import (
    ACCOUNT_BLOCKED,
    GENERIC_REGISTER,
    GENERIC_RESEND,
    GENERIC_RESET,
    LOGIN_FAILED,
    PHONE_UNAVAILABLE,
    TOKEN_INVALID,
    VERIFY_EMAIL,
    AccountStatus,
)
from apps.identity.models import ApiCredential, AuthSessionLink, IdentityEvent, IdentityToken, User
from apps.identity.services.secrets import hash_secret
from apps.identity.totp import decode_secret, totp_at

REGISTER = "/api/v1/auth/register/"
LOGIN = "/api/v1/auth/login/"
LOGOUT = "/api/v1/auth/logout/"
ME = "/api/v1/auth/me/"
VERIFY = "/api/v1/auth/email/verify/"
RESEND = "/api/v1/auth/email/verification/"
RESET = "/api/v1/auth/password/reset/"
RESET_CONFIRM = "/api/v1/auth/password/reset/confirm/"
CHANGE = "/api/v1/auth/password/change/"
REFRESH = "/api/v1/auth/token/refresh/"
REVOKE = "/api/v1/auth/revoke/"
PHONE = "/api/v1/auth/phone/"
PHONE_VERIFY = "/api/v1/auth/phone/verify/"
DEACTIVATE = "/api/v1/auth/deactivate/"
MFA_ENROLL = "/api/v1/auth/mfa/enroll/"
MFA_CONFIRM = "/api/v1/auth/mfa/confirm/"
MFA_VERIFY = "/api/v1/auth/mfa/verify/"
MFA_DISABLE = "/api/v1/auth/mfa/disable/"


@pytest.fixture(autouse=True)
def _identity_mail(locmem_email):
    return locmem_email


def _register(client, password, email="ada@example.com", **extra):
    body = {"email": email, "password": password, "first_name": "Ada", "last_name": "Lovelace"}
    body.update(extra)
    return client.post(REGISTER, body, format="json")


def _verify_latest(client):
    return client.post(VERIFY, {"token": latest_mail_value("token:")}, format="json")


def _login(client, password, email="ada@example.com", client_kind="web"):
    return client.post(
        LOGIN,
        {"email": email, "password": password, "client": client_kind},
        format="json",
    )


@pytest.mark.django_db
def test_registration_normalizes_email_and_stays_pending(api_client, password) -> None:
    response = _register(api_client, email="Ada@Example.com", password=password)

    assert response.status_code == 202
    assert response.json()["detail"] == GENERIC_REGISTER
    user = User.objects.get()
    assert user.email == "ada@example.com"
    assert user.status == AccountStatus.PENDING_VERIFICATION
    assert user.is_active is False
    assert user.is_staff is False
    assert user.email_verified_at is None
    assert user.password != password
    assert user.password.startswith("argon2")
    assert user.check_password(password)
    raw = latest_mail_value("token:")
    assert raw not in user.password
    assert IdentityToken.objects.filter(token_hash=hash_secret(raw)).exists()
    assert not IdentityToken.objects.filter(token_hash=raw).exists()


@pytest.mark.django_db
def test_duplicate_registration_returns_the_same_body(api_client, password) -> None:
    first = _register(api_client, password=password)
    mail_count = len(mail.outbox)
    second = _register(api_client, email="ADA@example.com", password=password)

    assert first.status_code == second.status_code == 202
    assert first.json()["detail"] == second.json()["detail"] == GENERIC_REGISTER
    assert User.objects.count() == 1
    assert len(mail.outbox) == mail_count + 1
    assert "token: " in mail.outbox[-1].body


@pytest.mark.django_db
def test_registration_of_an_active_account_sends_no_token(api_client, password) -> None:
    User.objects.create_user(email="ada@example.com", password=password)
    response = _register(api_client, password=password)

    assert response.status_code == 202
    assert response.json()["detail"] == GENERIC_REGISTER
    assert "token:" not in mail.outbox[-1].body
    assert "already exists" in mail.outbox[-1].body


@pytest.mark.django_db
def test_registration_rejects_a_weak_password_and_a_staff_flag(api_client, password) -> None:
    weak = _register(api_client, "password", is_staff=True, status="active")

    assert weak.status_code == 400
    assert User.objects.count() == 0

    created = _register(api_client, password, is_staff=True, is_superuser=True)
    assert created.status_code == 202
    user = User.objects.get()
    assert user.is_staff is False
    assert user.is_superuser is False
    assert user.status == AccountStatus.PENDING_VERIFICATION


@pytest.mark.django_db
def test_login_uses_one_message_for_unknown_and_wrong_passwords(api_client, password) -> None:
    _register(api_client, password=password)
    unknown = _login(api_client, email="missing@example.com", password=password)
    wrong = _login(api_client, password="Other-Bridge-Kestrel-99")

    assert unknown.status_code == wrong.status_code == 401
    assert unknown.json()["errors"] == wrong.json()["errors"]
    assert error_detail(unknown) == LOGIN_FAILED
    assert password not in unknown.content.decode()
    assert IdentityEvent.objects.filter(event_type="login_failed", user__isnull=True).exists()
    attached = IdentityEvent.objects.get(event_type="login_failed", user__isnull=False)
    assert attached.user.email == "ada@example.com"


@pytest.mark.django_db
def test_pending_account_cannot_login_until_the_email_token_is_used(api_client, password) -> None:
    _register(api_client, password=password)
    blocked = _login(api_client, password=password)
    assert blocked.status_code == 403
    assert error_detail(blocked) == VERIFY_EMAIL

    raw = latest_mail_value("token:")
    verified = api_client.post(VERIFY, {"token": raw}, format="json")
    replay = api_client.post(VERIFY, {"token": raw}, format="json")

    assert verified.status_code == 200
    assert verified.json()["email_verified"] is True
    assert verified.json()["status"] == AccountStatus.ACTIVE
    assert replay.status_code == 400
    assert error_detail(replay) == TOKEN_INVALID
    signed_in = _login(api_client, password=password)
    assert signed_in.status_code == 200
    assert signed_in.json()["user"]["email"] == "ada@example.com"
    assert "access_token" not in signed_in.json()


@pytest.mark.django_db
def test_expired_and_replaced_email_tokens_do_not_verify(api_client, password) -> None:
    _register(api_client, password=password)
    first = latest_mail_value("token:")
    resent = api_client.post(RESEND, {"email": "ADA@example.com"}, format="json")
    assert resent.status_code == 202
    assert resent.json()["detail"] == GENERIC_RESEND
    second = latest_mail_value("token:")
    assert first != second

    stale = api_client.post(VERIFY, {"token": first}, format="json")
    assert stale.status_code == 400

    token = IdentityToken.objects.get(token_hash=hash_secret(second))
    token.expires_at = timezone.now() - timedelta(seconds=1)
    token.save(update_fields=["expires_at"])
    expired = api_client.post(VERIFY, {"token": second}, format="json")
    assert expired.status_code == 400
    assert User.objects.get().status == AccountStatus.PENDING_VERIFICATION

    unknown = api_client.post(RESEND, {"email": "nobody@example.com"}, format="json")
    assert unknown.status_code == 202
    assert unknown.json()["detail"] == resent.json()["detail"]


@pytest.mark.django_db
def test_logout_and_profile_are_limited_to_the_current_user(api_client, password) -> None:
    anonymous = api_client.get(ME)
    assert anonymous.status_code == 401

    _register(api_client, password=password)
    _verify_latest(api_client)
    signed_in = _login(api_client, password=password)
    assert signed_in.status_code == 200

    other = User.objects.create_user(email="bea@example.com", password=password, first_name="Bea")
    changed = api_client.patch(
        ME,
        {
            "first_name": "Augusta",
            "email": "root@example.com",
            "is_staff": True,
            "status": "suspended",
        },
        format="json",
    )
    assert changed.status_code == 200
    assert changed.json()["first_name"] == "Augusta"
    assert changed.json()["email"] == "ada@example.com"
    assert changed.json()["is_staff"] is False
    assert changed.json()["status"] == AccountStatus.ACTIVE
    other.refresh_from_db()
    assert other.first_name == "Bea"

    logged_out = api_client.post(LOGOUT, format="json")
    assert logged_out.status_code == 200
    assert api_client.get(ME).status_code == 401


@pytest.mark.django_db
def test_login_cycles_the_session_key(api_client, password) -> None:
    User.objects.create_user(email="ada@example.com", password=password)
    api_client.get("/api/v1/auth/csrf/")
    session = api_client.session
    session["seed"] = "before-login"
    session.save()
    previous = api_client.session.session_key

    response = _login(api_client, password=password)

    assert response.status_code == 200
    current = api_client.session.session_key
    assert previous
    assert current != previous
    assert not Session.objects.filter(session_key=previous).exists()
    link = AuthSessionLink.objects.get()
    assert link.session_key == current
    assert link.user.email == "ada@example.com"


@pytest.mark.django_db
def test_password_reset_is_generic_and_one_time(api_client, password) -> None:
    User.objects.create_user(email="ada@example.com", password=password, first_name="Ada")
    unknown = api_client.post(RESET, {"email": "nobody@example.com"}, format="json")
    known = api_client.post(RESET, {"email": "Ada@Example.com"}, format="json")

    assert unknown.status_code == known.status_code == 202
    assert unknown.json()["detail"] == known.json()["detail"] == GENERIC_RESET
    assert len(mail.outbox) == 1
    first = latest_mail_value("token:")
    api_client.post(RESET, {"email": "ada@example.com"}, format="json")
    second = latest_mail_value("token:")
    replaced = api_client.post(
        RESET_CONFIRM,
        {"token": first, "password": password + "-new"},
        format="json",
    )
    assert replaced.status_code == 400

    weak = api_client.post(RESET_CONFIRM, {"token": second, "password": "password"}, format="json")
    assert weak.status_code == 400
    assert User.objects.get().check_password(password)

    updated = api_client.post(
        RESET_CONFIRM,
        {"token": second, "password": password + "-New"},
        format="json",
    )
    assert updated.status_code == 200
    user = User.objects.get()
    assert user.check_password(password + "-New")
    replay = api_client.post(
        RESET_CONFIRM,
        {"token": second, "password": password + "-Again"},
        format="json",
    )
    assert replay.status_code == 400
    assert user.check_password(password + "-New")


@pytest.mark.django_db
def test_reset_respects_deactivated_and_suspended_accounts(api_client, password) -> None:
    user = User.objects.create_user(email="ada@example.com", password=password)
    user.status = AccountStatus.DEACTIVATED
    user.save()
    api_client.post(RESET, {"email": user.email}, format="json")
    restored = api_client.post(
        RESET_CONFIRM,
        {"token": latest_mail_value("token:"), "password": password + "-New"},
        format="json",
    )
    user.refresh_from_db()
    assert restored.status_code == 200
    assert user.status == AccountStatus.ACTIVE
    assert _login(api_client, password=password + "-New").status_code == 200

    user.status = AccountStatus.SUSPENDED
    user.save()
    anonymous = APIClient()
    ignored = anonymous.post(RESET, {"email": user.email}, format="json")
    assert ignored.status_code == 202
    assert len(mail.outbox) == 1
    assert IdentityEvent.objects.filter(event_type="password_reset_ignored").exists()

    user.status = AccountStatus.ACTIVE
    user.save()
    anonymous.post(RESET, {"email": user.email}, format="json")
    raw = latest_mail_value("token:")
    user.status = AccountStatus.SUSPENDED
    user.save()
    refused = anonymous.post(
        RESET_CONFIRM,
        {"token": raw, "password": password + "-Later"},
        format="json",
    )
    user.refresh_from_db()
    assert refused.status_code == 400
    assert error_detail(refused) == TOKEN_INVALID
    assert user.check_password(password + "-New")
    assert user.status == AccountStatus.SUSPENDED

    user.status = AccountStatus.ACTIVE
    user.save()
    accepted = anonymous.post(
        RESET_CONFIRM,
        {"token": raw, "password": password + "-Later"},
        format="json",
    )
    assert accepted.status_code == 200


@pytest.mark.django_db
def test_password_change_keeps_only_the_current_session(api_client, password) -> None:
    User.objects.create_user(email="ada@example.com", password=password)
    other = APIClient()
    mobile = APIClient()
    assert _login(api_client, password=password).status_code == 200
    assert _login(other, password=password).status_code == 200
    issued = _login(mobile, password=password, client_kind="mobile")
    access = issued.json()["access_token"]

    changed = api_client.post(
        CHANGE,
        {"current_password": password, "new_password": password + "-New"},
        format="json",
    )
    assert changed.status_code == 200
    assert api_client.get(ME).status_code == 200
    assert other.get(ME).status_code == 401
    mobile.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
    assert mobile.get(ME).status_code == 401
    assert _login(other, password=password).status_code == 401
    assert _login(other, password=password + "-New").status_code == 200


@pytest.mark.django_db
def test_mobile_refresh_rotates_and_replay_revokes_the_family(password) -> None:
    User.objects.create_user(email="ada@example.com", password=password)
    client = APIClient()
    issued = _login(client, password=password, client_kind="mobile")
    first_access = issued.json()["access_token"]
    first_refresh = issued.json()["refresh_token"]
    assert issued.json()["expires_in"] == 900

    reader = APIClient()
    reader.credentials(HTTP_AUTHORIZATION=f"Bearer {first_access}")
    assert reader.get(ME).json()["email"] == "ada@example.com"
    reader.credentials(HTTP_AUTHORIZATION=f"Bearer {first_refresh}")
    assert reader.get(ME).status_code == 401

    rotated = client.post(REFRESH, {"refresh_token": first_refresh}, format="json")
    assert rotated.status_code == 200
    second_access = rotated.json()["access_token"]
    second_refresh = rotated.json()["refresh_token"]
    assert second_access != first_access
    assert second_refresh != first_refresh

    reader.credentials(HTTP_AUTHORIZATION=f"Bearer {first_access}")
    assert reader.get(ME).status_code == 401
    reader.credentials(HTTP_AUTHORIZATION=f"Bearer {second_access}")
    assert reader.get(ME).status_code == 200

    replay = client.post(REFRESH, {"refresh_token": first_refresh}, format="json")
    assert replay.status_code == 401
    reader.credentials(HTTP_AUTHORIZATION=f"Bearer {second_access}")
    assert reader.get(ME).status_code == 401
    assert client.post(REFRESH, {"refresh_token": second_refresh}, format="json").status_code == 401
    assert ApiCredential.objects.filter(revoked_at__isnull=True).count() == 0


@pytest.mark.django_db
def test_phone_verification_is_optional_and_unique_only_after_success(api_client, password) -> None:
    _register(api_client, password=password, phone_e164="0712 345 678")
    _verify_latest(api_client)
    _login(api_client, password=password)
    user = User.objects.get(email="ada@example.com")
    assert user.phone_e164 == "+254712345678"
    assert user.phone_verified_at is None
    assert not any(message.subject.startswith("GLITTERS phone") for message in mail.outbox)

    other = APIClient()
    User.objects.create_user(email="bea@example.com", password=password)
    _login(other, email="bea@example.com", password=password)
    assert other.post(PHONE, {"phone_e164": "0712345678"}, format="json").status_code == 202
    bea_code = latest_mail_value("code:")
    assert api_client.post(PHONE, {"phone_e164": "+254712345678"}, format="json").status_code == 202
    ada_code = latest_mail_value("code:")
    assert User.objects.filter(phone_e164="+254712345678").count() == 2

    for _ in range(4):
        failed = other.post(PHONE_VERIFY, {"code": "000000"}, format="json")
        assert failed.status_code == 400
    verified = other.post(PHONE_VERIFY, {"code": bea_code}, format="json")
    assert verified.status_code == 200
    assert verified.json()["phone_verified"] is True
    user.refresh_from_db()
    assert user.phone_e164 == ""
    assert user.phone_verified_at is None

    claimed = api_client.post(PHONE, {"phone_e164": "+254712345678"}, format="json")
    assert claimed.status_code == 202
    conflict = api_client.post(
        PHONE_VERIFY,
        {"code": latest_mail_value("code:")},
        format="json",
    )
    assert conflict.status_code == 400
    assert error_detail(conflict) == PHONE_UNAVAILABLE
    user.refresh_from_db()
    assert user.phone_verified_at is None
    assert ada_code != bea_code


@pytest.mark.django_db
def test_phone_codes_expire_and_burn_after_five_failures(api_client, password) -> None:
    User.objects.create_user(email="ada@example.com", password=password)
    _login(api_client, password=password)
    international = api_client.post(PHONE, {"phone_e164": "+1 415 555 2671"}, format="json")
    assert international.status_code == 202
    assert User.objects.get().phone_e164 == "+14155552671"
    rejected = api_client.post(PHONE, {"phone_e164": "123"}, format="json")
    assert rejected.status_code == 400

    code = latest_mail_value("code:")
    for _ in range(5):
        failed = api_client.post(PHONE_VERIFY, {"code": "000000"}, format="json")
        assert failed.status_code == 400
    burned = api_client.post(PHONE_VERIFY, {"code": code}, format="json")
    assert burned.status_code == 400
    assert User.objects.get().phone_verified_at is None

    api_client.post(PHONE, {"phone_e164": "+14155552671"}, format="json")
    fresh = latest_mail_value("code:")
    stored = IdentityToken.objects.filter(purpose="phone_verification", used_at__isnull=True).get()
    stored.expires_at = timezone.now() - timedelta(seconds=1)
    stored.save(update_fields=["expires_at"])
    assert api_client.post(PHONE_VERIFY, {"code": fresh}, format="json").status_code == 400


@pytest.mark.django_db
def test_mfa_enrollment_challenge_recovery_and_disable(api_client, password) -> None:
    User.objects.create_user(email="ada@example.com", password=password)
    assert _login(api_client, password=password).status_code == 200
    enrolled = api_client.post(MFA_ENROLL, {}, format="json")
    assert enrolled.status_code == 200
    secret = decode_secret(enrolled.json()["secret"])
    confirmed = api_client.post(MFA_CONFIRM, {"code": totp_at(secret)}, format="json")
    assert confirmed.status_code == 200
    recovery = confirmed.json()["recovery_codes"]
    assert len(recovery) == 8

    api_client.post(LOGOUT, format="json")
    challenged = _login(api_client, password=password)
    assert challenged.status_code == 200
    assert challenged.json()["mfa_required"] is True
    assert "secret" not in challenged.json()
    challenge = challenged.json()["challenge_token"]
    completed = api_client.post(
        MFA_VERIFY,
        {"challenge_token": challenge, "code": recovery[0]},
        format="json",
    )
    assert completed.status_code == 200
    assert api_client.get(ME).status_code == 200

    api_client.post(LOGOUT, format="json")
    again = _login(api_client, password=password)
    reused = api_client.post(
        MFA_VERIFY,
        {"challenge_token": again.json()["challenge_token"], "code": recovery[0]},
        format="json",
    )
    assert reused.status_code == 400

    fresh = _login(APIClient(), password=password)
    entered = APIClient()
    signed = entered.post(
        MFA_VERIFY,
        {"challenge_token": fresh.json()["challenge_token"], "code": totp_at(secret)},
        format="json",
    )
    assert signed.status_code == 200
    disabled = entered.post(
        MFA_DISABLE,
        {"password": password, "code": totp_at(secret)},
        format="json",
    )
    assert disabled.status_code == 200
    assert _login(APIClient(), password=password).json()["user"]["mfa_enabled"] is False


@pytest.mark.django_db
def test_staff_must_confirm_mfa_before_an_api_session(password) -> None:
    User.objects.create_superuser(email="ops@example.com", password=password)
    client = APIClient()
    login = _login(client, email="ops@example.com", password=password)
    assert login.status_code == 200
    assert login.json()["mfa_enrollment_required"] is True
    assert client.get(ME).status_code == 401

    enrolled = client.post(
        MFA_ENROLL,
        {"enrollment_token": login.json()["enrollment_token"]},
        format="json",
    )
    secret = decode_secret(enrolled.json()["secret"])
    confirmed = client.post(
        MFA_CONFIRM,
        {"enrollment_token": login.json()["enrollment_token"], "code": totp_at(secret)},
        format="json",
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["user"]["mfa_enabled"] is True
    assert "recovery_codes" in confirmed.json()
    assert client.get(ME).status_code == 200

    refused = client.post(
        MFA_DISABLE,
        {"password": password, "code": totp_at(secret)},
        format="json",
    )
    assert refused.status_code == 403
    assert User.objects.get().has_confirmed_mfa() is True


@pytest.mark.django_db
def test_five_wrong_mfa_codes_burn_the_challenge(api_client, password) -> None:
    User.objects.create_user(email="ada@example.com", password=password)
    _login(api_client, password=password)
    enrolled = api_client.post(MFA_ENROLL, {}, format="json")
    secret = decode_secret(enrolled.json()["secret"])
    api_client.post(MFA_CONFIRM, {"code": totp_at(secret)}, format="json")
    api_client.post(LOGOUT, format="json")
    challenged = _login(api_client, password=password)
    token = challenged.json()["challenge_token"]
    for _ in range(4):
        failed = api_client.post(
            MFA_VERIFY,
            {"challenge_token": token, "code": "000000"},
            format="json",
        )
        assert failed.status_code == 400
    assert (
        api_client.post(
            MFA_VERIFY,
            {"challenge_token": token, "code": totp_at(secret)},
            format="json",
        ).status_code
        == 200
    )

    burned_login = _login(APIClient(), password=password)
    burned = burned_login.json()["challenge_token"]
    caller = APIClient()
    for _ in range(5):
        failed = caller.post(
            MFA_VERIFY,
            {"challenge_token": burned, "code": "000000"},
            format="json",
        )
        assert failed.status_code == 400
    assert (
        caller.post(
            MFA_VERIFY,
            {"challenge_token": burned, "code": totp_at(secret)},
            format="json",
        ).status_code
        == 400
    )


@pytest.mark.django_db
def test_suspension_and_deactivation_block_access(api_client, password) -> None:
    User.objects.create_user(email="ada@example.com", password=password)
    _login(api_client, password=password)
    user = User.objects.get()
    user.status = AccountStatus.SUSPENDED
    user.save()
    blocked = api_client.get(ME)
    assert blocked.status_code == 403
    assert error_detail(blocked) == ACCOUNT_BLOCKED
    assert api_client.get(ME).status_code == 401

    user.status = AccountStatus.ACTIVE
    user.save()
    _login(api_client, password=password)
    rejected = api_client.post(DEACTIVATE, {"password": "wrong-password"}, format="json")
    assert rejected.status_code == 400
    assert User.objects.get().status == AccountStatus.ACTIVE
    closed = api_client.post(DEACTIVATE, {"password": password}, format="json")
    assert closed.status_code == 200
    assert User.objects.get().status == AccountStatus.DEACTIVATED
    assert api_client.get(ME).status_code in {401, 403}
    denied = _login(api_client, password=password)
    assert denied.status_code == 403
    assert error_detail(denied) == ACCOUNT_BLOCKED


@pytest.mark.django_db
def test_revoke_can_keep_or_end_the_current_browser_session(password) -> None:
    User.objects.create_user(email="ada@example.com", password=password)
    current = APIClient()
    other = APIClient()
    mobile = APIClient()
    _login(current, password=password)
    _login(other, password=password)
    access = _login(mobile, password=password, client_kind="mobile").json()["access_token"]

    kept = current.post(REVOKE, {"scope": "others"}, format="json")
    assert kept.status_code == 200
    assert current.get(ME).status_code == 200
    assert other.get(ME).status_code == 401
    mobile.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
    assert mobile.get(ME).status_code == 401

    _login(other, password=password)
    ended = current.post(REVOKE, {"scope": "all"}, format="json")
    assert ended.status_code == 200
    assert current.get(ME).status_code == 401
    assert other.get(ME).status_code == 401


@pytest.mark.django_db
def test_mobile_logout_revokes_the_bearer_token(password) -> None:
    User.objects.create_user(email="ada@example.com", password=password)
    client = APIClient()
    access = _login(client, password=password, client_kind="mobile").json()["access_token"]
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
    assert client.post(LOGOUT, format="json").status_code == 200
    assert client.get(ME).status_code == 401


@pytest.mark.django_db
def test_security_restricted_login_is_blocked_until_reset(api_client, password) -> None:
    user = User.objects.create_user(email="ada@example.com", password=password)
    user.status = AccountStatus.SECURITY_RESTRICTED
    user.save()
    blocked = _login(api_client, password=password)
    assert blocked.status_code == 403
    assert error_detail(blocked) == ACCOUNT_BLOCKED

    api_client.post(RESET, {"email": user.email}, format="json")
    confirmed = api_client.post(
        RESET_CONFIRM,
        {"token": latest_mail_value("token:"), "password": password + "-New"},
        format="json",
    )
    assert confirmed.status_code == 200
    user.refresh_from_db()
    assert user.status == AccountStatus.ACTIVE
    assert _login(api_client, password=password + "-New").status_code == 200


@pytest.mark.django_db
def test_audit_rows_do_not_store_passwords_or_tokens(api_client, password) -> None:
    _register(api_client, password=password)
    raw = latest_mail_value("token:")
    for event in IdentityEvent.objects.all():
        blob = f"{event.event_type}|{event.user_agent}|{event.ip_address}"
        assert password not in blob
        assert raw not in blob
        assert "token" not in event.event_type
