"""User creation rules for the identity aggregate."""

from __future__ import annotations

from django.contrib.auth.base_user import BaseUserManager
from django.contrib.auth.password_validation import validate_password
from django.utils import timezone

from apps.identity.domain import AccountStatus, normalize_email


class UserManager(BaseUserManager):
    use_in_migrations = True

    def create_user(self, email: str, password: str | None = None, **extra_fields):
        extra_fields.setdefault("status", AccountStatus.ACTIVE)
        if extra_fields.get("status") != AccountStatus.ACTIVE:
            extra_fields["is_active"] = False
        return self._create_user(email, password, allow_superuser=False, **extra_fields)

    def create_superuser(self, email: str, password: str | None = None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields["status"] = AccountStatus.ACTIVE
        extra_fields["is_active"] = True
        extra_fields.setdefault("email_verified_at", timezone.now())
        if extra_fields.get("is_staff") is not True:
            raise ValueError("A superuser must have is_staff=True.")
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("A superuser must have is_superuser=True.")
        return self._create_user(email, password, allow_superuser=True, **extra_fields)

    def get_by_natural_key(self, username):
        return self.get(email=normalize_email(username))

    def _create_user(
        self,
        email: str,
        password: str | None,
        *,
        allow_superuser: bool,
        **extra_fields,
    ):
        normalized = normalize_email(email)
        if not normalized:
            raise ValueError("An email address is required.")
        if not password:
            raise ValueError("A password is required.")
        if extra_fields.get("is_superuser") and not allow_superuser:
            raise ValueError("Use create_superuser to create a superuser.")
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        user = self.model(email=normalized, **extra_fields)
        validate_password(password, user)
        user.set_password(password)
        user.full_clean()
        user.save(using=self._db)
        return user
