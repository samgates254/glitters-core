"""Domain rules that do not need the HTTP stack."""

import pytest
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.utils import timezone

from apps.identity.domain import (
    AUTH_THROTTLE_RATES,
    AccountStatus,
    normalize_phone,
    transition_allowed,
)
from apps.identity.models import User
from apps.identity.totp import otpauth_uri, totp_at, verify_totp


def test_auth_user_model_remains_identity_user() -> None:
    assert settings.AUTH_USER_MODEL == "identity.User"


def test_throttle_rates_are_explicit() -> None:
    assert AUTH_THROTTLE_RATES == {
        "auth_login": "10/min",
        "auth_register": "5/hour",
        "auth_password_reset": "5/hour",
        "auth_verify": "10/hour",
        "auth_mfa": "10/min",
        "auth_phone": "5/hour",
    }


def test_kenyan_numbers_normalize_to_e164() -> None:
    assert normalize_phone("0712 345 678") == "+254712345678"
    assert normalize_phone("(0712) 345-678") == "+254712345678"
    assert normalize_phone("712345678") == "+254712345678"
    assert normalize_phone("254712345678") == "+254712345678"
    assert normalize_phone("0112345678") == "+254112345678"
    assert normalize_phone("00 254 712 345 678") == "+254712345678"


def test_international_numbers_require_a_country_code() -> None:
    assert normalize_phone("+1 415 555 2671") == "+14155552671"
    with pytest.raises(ValueError):
        normalize_phone("4155552671")
    with pytest.raises(ValueError):
        normalize_phone("+12")
    with pytest.raises(ValueError):
        normalize_phone("not-a-phone")


def test_totp_accepts_the_current_step_and_one_neighbour() -> None:
    secret = bytes.fromhex("3132333435363738393031323334353637383930")
    moment = 1_700_000_000.0
    current = totp_at(secret, moment)

    assert verify_totp(secret, current, moment=moment)
    assert verify_totp(secret, totp_at(secret, moment - 30), moment=moment)
    assert not verify_totp(secret, totp_at(secret, moment - 90), moment=moment)
    assert not verify_totp(secret, "000000", moment=moment)
    assert "algorithm=SHA1" in otpauth_uri(secret=secret, account="ada@example.com")


def test_status_transitions_match_the_lifecycle() -> None:
    assert transition_allowed(AccountStatus.PENDING_VERIFICATION, AccountStatus.ACTIVE)
    assert not transition_allowed(AccountStatus.PENDING_VERIFICATION, AccountStatus.SUSPENDED)
    assert not transition_allowed(AccountStatus.SUSPENDED, AccountStatus.DEACTIVATED)
    assert transition_allowed(AccountStatus.DEACTIVATED, AccountStatus.ACTIVE)
    assert transition_allowed(
        AccountStatus.SUSPENDED,
        AccountStatus.DEACTIVATED,
        force=True,
    )


@pytest.mark.django_db
def test_public_registration_status_is_not_the_manager_default(password: str) -> None:
    created = User.objects.create_user(email="ada@example.com", password=password)

    assert created.status == AccountStatus.ACTIVE
    assert created.is_active is True
    created.status = AccountStatus.PENDING_VERIFICATION
    created.save()
    created.refresh_from_db()
    assert created.is_active is False


@pytest.mark.django_db
def test_unverified_phones_may_repeat_and_verified_phones_may_not(password: str) -> None:
    first = User.objects.create_user(email="ada@example.com", password=password)
    second = User.objects.create_user(email="bea@example.com", password=password)
    first.phone_e164 = "0712345678"
    second.phone_e164 = "+254712345678"
    first.save()
    second.save()
    first.refresh_from_db()
    second.refresh_from_db()
    assert first.phone_e164 == second.phone_e164 == "+254712345678"

    now = timezone.now()
    first.phone_verified_at = now
    first.save()
    second.phone_verified_at = now
    with pytest.raises(IntegrityError), transaction.atomic():
        second.save()


@pytest.mark.django_db
def test_clearing_a_phone_clears_verification(password: str) -> None:
    user = User.objects.create_user(email="ada@example.com", password=password)
    user.phone_e164 = "+254712345678"
    user.phone_verified_at = timezone.now()
    user.save()
    user.phone_e164 = ""
    user.save()
    user.refresh_from_db()

    assert user.phone_e164 == ""
    assert user.phone_verified_at is None


@pytest.mark.django_db
def test_verified_phone_constraint_rejects_an_empty_number(password: str) -> None:
    user = User.objects.create_user(email="ada@example.com", password=password)
    with pytest.raises(IntegrityError), transaction.atomic():
        User.objects.filter(pk=user.pk).update(
            phone_e164="",
            phone_verified_at=timezone.now(),
        )


@pytest.mark.django_db
def test_there_is_still_no_default_auth_user_table(password: str) -> None:
    User.objects.create_user(email="ada@example.com", password=password)
    with connection.cursor() as cursor:
        cursor.execute("SELECT to_regclass('public.auth_user')")
        assert cursor.fetchone()[0] is None


def test_blank_phone_is_rejected_by_normalize() -> None:
    with pytest.raises(ValueError):
        normalize_phone("   ")


@pytest.mark.django_db
def test_password_validator_still_rejects_a_common_password() -> None:
    with pytest.raises(ValidationError):
        User.objects.create_user(email="ada@example.com", password="password")
