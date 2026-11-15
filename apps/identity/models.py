"""Platform identity.

A user is a person who can authenticate. Organization membership, property
ownership, and listing authority are separate relationships and are not
inferred from this record.
"""

from __future__ import annotations

import uuid

from django.conf import settings
from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.identity.domain import AccountStatus, normalize_email, normalize_phone
from apps.identity.managers import UserManager


class User(AbstractBaseUser, PermissionsMixin):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    email = models.EmailField(_("email address"), unique=True)
    first_name = models.CharField(_("first name"), max_length=150, blank=True)
    last_name = models.CharField(_("last name"), max_length=150, blank=True)
    status = models.CharField(
        max_length=32,
        choices=AccountStatus.CHOICES,
        default=AccountStatus.ACTIVE,
    )
    phone_e164 = models.CharField(max_length=16, blank=True, default="")
    phone_verified_at = models.DateTimeField(null=True, blank=True)
    email_verified_at = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    date_joined = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS: list[str] = []

    class Meta:
        db_table = "identity_user"
        verbose_name = _("user")
        verbose_name_plural = _("users")
        ordering = ["email"]
        constraints = [
            models.CheckConstraint(
                condition=~models.Q(email=""),
                name="identity_user_email_not_empty",
            ),
            models.CheckConstraint(
                condition=models.Q(status__in=sorted(AccountStatus.VALUES)),
                name="identity_user_status_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(phone_e164="")
                | models.Q(phone_e164__regex=r"^\+[1-9]\d{7,14}$"),
                name="identity_user_phone_e164",
            ),
            models.CheckConstraint(
                condition=models.Q(phone_verified_at__isnull=True) | ~models.Q(phone_e164=""),
                name="identity_user_verified_phone_present",
            ),
            models.UniqueConstraint(
                fields=["phone_e164"],
                condition=models.Q(phone_verified_at__isnull=False) & ~models.Q(phone_e164=""),
                name="identity_user_verified_phone_unique",
            ),
        ]

    def save(self, *args, **kwargs):
        self.email = normalize_email(self.email)
        if self.phone_e164:
            self.phone_e164 = normalize_phone(self.phone_e164)
        else:
            self.phone_e164 = ""
            self.phone_verified_at = None
        self.is_active = self.status == AccountStatus.ACTIVE
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return self.email

    def get_full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()

    def get_short_name(self) -> str:
        return self.first_name or self.email

    def has_confirmed_mfa(self) -> bool:
        return self.mfa_devices.filter(
            confirmed_at__isnull=False,
            disabled_at__isnull=True,
        ).exists()


class TokenPurpose:
    EMAIL_VERIFICATION = "email_verification"
    PASSWORD_RESET = "password_reset"  # noqa: S105
    PHONE_VERIFICATION = "phone_verification"
    MFA_CHALLENGE = "mfa_challenge"
    MFA_ENROLLMENT = "mfa_enrollment"

    CHOICES = (
        (EMAIL_VERIFICATION, "Email verification"),
        (PASSWORD_RESET, "Password reset"),
        (PHONE_VERIFICATION, "Phone verification"),
        (MFA_CHALLENGE, "MFA challenge"),
        (MFA_ENROLLMENT, "MFA enrollment"),
    )


class IdentityToken(models.Model):
    """One-time secret stored only as a SHA-256 hash."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="identity_tokens",
    )
    purpose = models.CharField(max_length=32, choices=TokenPurpose.CHOICES)
    token_hash = models.CharField(max_length=64, unique=True)
    subject = models.CharField(max_length=32, blank=True, default="")
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)
    attempt_count = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "identity_token"
        indexes = [
            models.Index(fields=["user", "purpose"], name="identity_token_user_purpose"),
        ]


class ApiCredential(models.Model):
    """Mobile access and refresh secrets. Browser sessions use AuthSessionLink."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="api_credentials",
    )
    family_id = models.UUIDField(default=uuid.uuid4, editable=False)
    access_hash = models.CharField(max_length=64, unique=True)
    refresh_hash = models.CharField(max_length=64, unique=True)
    access_expires_at = models.DateTimeField()
    refresh_expires_at = models.DateTimeField()
    revoked_at = models.DateTimeField(null=True, blank=True)
    client_label = models.CharField(max_length=32, default="mobile")
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "identity_api_credential"
        indexes = [
            models.Index(fields=["family_id"], name="identity_credential_family"),
        ]


class AuthSessionLink(models.Model):
    """Maps a Django session key to a user so revocation does not scan every session."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="auth_sessions",
    )
    session_key = models.CharField(max_length=40, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "identity_session"


class MfaDevice(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="mfa_devices",
    )
    secret_signed = models.TextField()
    confirmed_at = models.DateTimeField(null=True, blank=True)
    disabled_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "identity_mfa_device"


class RecoveryCode(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="recovery_codes",
    )
    code_hash = models.CharField(max_length=64, unique=True)
    used_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "identity_recovery_code"


class IdentityEvent(models.Model):
    """Append-only authentication audit record. It stores no secrets."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="identity_events",
    )
    event_type = models.CharField(max_length=64)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=200, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "identity_event"
        indexes = [
            models.Index(fields=["user", "created_at"], name="identity_event_user_created"),
        ]
