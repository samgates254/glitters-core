import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from apps.identity.admin import EmailUserCreationForm
from apps.identity.models import User


@pytest.mark.django_db
def test_user_email_is_normalized_and_password_is_hashed(password: str) -> None:
    user = User.objects.create_user(email=" Ada@Example.com ", password=password)

    assert user.email == "ada@example.com"
    assert user.password != password
    assert user.check_password(password)
    assert user.is_staff is False
    assert user.is_superuser is False
    assert user.id is not None


@pytest.mark.django_db
def test_duplicate_email_is_rejected_before_insert(password: str) -> None:
    User.objects.create_user(email="ada@example.com", password=password)

    with pytest.raises(ValidationError):
        User.objects.create_user(email="Ada@Example.com", password=password)


@pytest.mark.django_db
def test_database_rejects_a_duplicate_email(password: str) -> None:
    User.objects.create_user(email="ada@example.com", password=password)

    with pytest.raises(IntegrityError), transaction.atomic():
        User.objects.bulk_create([User(email="ada@example.com", password="not-a-login")])


@pytest.mark.django_db
def test_weak_password_is_rejected() -> None:
    with pytest.raises(ValidationError):
        User.objects.create_user(email="ada@example.com", password="password")


@pytest.mark.django_db
def test_create_user_cannot_mint_a_superuser(password: str) -> None:
    with pytest.raises(ValueError, match="create_superuser"):
        User.objects.create_user(
            email="ada@example.com",
            password=password,
            is_superuser=True,
        )


@pytest.mark.django_db
def test_create_superuser_sets_privileged_flags(password: str) -> None:
    user = User.objects.create_superuser(email="ops@example.com", password=password)

    assert user.is_staff is True
    assert user.is_superuser is True
    assert user.is_active is True


@pytest.mark.django_db
def test_create_superuser_rejects_a_demoted_staff_flag(password: str) -> None:
    with pytest.raises(ValueError, match="is_staff"):
        User.objects.create_superuser(
            email="ops@example.com",
            password=password,
            is_staff=False,
        )


def test_blank_email_is_rejected(password: str) -> None:
    with pytest.raises(ValueError, match="email"):
        User.objects.create_user(email="  ", password=password)


@pytest.mark.django_db
def test_admin_creation_form_creates_an_email_user(password: str) -> None:
    form = EmailUserCreationForm(
        data={
            "email": "New.User@Example.com",
            "password1": password,
            "password2": password,
        }
    )

    assert form.is_valid(), form.errors
    user = form.save()
    assert user.email == "new.user@example.com"
    assert user.check_password(password)
