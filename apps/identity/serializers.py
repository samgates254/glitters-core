"""Request validation for identity endpoints. Responses are built in the views."""

from __future__ import annotations

from rest_framework import serializers


class MessageSerializer(serializers.Serializer):
    detail = serializers.CharField()


class UserPublicSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    email = serializers.EmailField()
    first_name = serializers.CharField()
    last_name = serializers.CharField()
    status = serializers.CharField()
    phone_e164 = serializers.CharField()
    email_verified = serializers.BooleanField()
    phone_verified = serializers.BooleanField()
    mfa_enabled = serializers.BooleanField()
    is_staff = serializers.BooleanField()


class RegisterSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(trim_whitespace=False, write_only=True)
    first_name = serializers.CharField(max_length=150, allow_blank=True, required=False, default="")
    last_name = serializers.CharField(max_length=150, allow_blank=True, required=False, default="")
    phone_e164 = serializers.CharField(max_length=32, allow_blank=True, required=False, default="")


class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(trim_whitespace=False, write_only=True)
    client = serializers.ChoiceField(choices=["web", "mobile"], default="web")


class TokenSerializer(serializers.Serializer):
    token = serializers.CharField()


class CodeSerializer(serializers.Serializer):
    code = serializers.CharField()


class MfaVerifySerializer(serializers.Serializer):
    challenge_token = serializers.CharField()
    code = serializers.CharField()


class MfaEnrollSerializer(serializers.Serializer):
    enrollment_token = serializers.CharField(required=False, allow_blank=True, default="")


class MfaConfirmSerializer(serializers.Serializer):
    code = serializers.CharField()
    enrollment_token = serializers.CharField(required=False, allow_blank=True, default="")


class MfaDisableSerializer(serializers.Serializer):
    password = serializers.CharField(trim_whitespace=False)
    code = serializers.CharField()


class EmailSerializer(serializers.Serializer):
    email = serializers.EmailField()


class PasswordResetConfirmSerializer(serializers.Serializer):
    token = serializers.CharField()
    password = serializers.CharField(trim_whitespace=False)


class PasswordChangeSerializer(serializers.Serializer):
    current_password = serializers.CharField(trim_whitespace=False)
    new_password = serializers.CharField(trim_whitespace=False)


class RefreshSerializer(serializers.Serializer):
    refresh_token = serializers.CharField()


class RevokeSerializer(serializers.Serializer):
    scope = serializers.ChoiceField(choices=["others", "all"], default="others")


class ProfileSerializer(serializers.Serializer):
    first_name = serializers.CharField(max_length=150, allow_blank=True, required=False)
    last_name = serializers.CharField(max_length=150, allow_blank=True, required=False)


class PhoneSerializer(serializers.Serializer):
    phone_e164 = serializers.CharField(max_length=32, allow_blank=True)


class PasswordSerializer(serializers.Serializer):
    password = serializers.CharField(trim_whitespace=False)


def public_user(user) -> dict:
    return UserPublicSerializer(
        {
            "id": user.id,
            "email": user.email,
            "first_name": user.first_name,
            "last_name": user.last_name,
            "status": user.status,
            "phone_e164": user.phone_e164,
            "email_verified": user.email_verified_at is not None,
            "phone_verified": user.phone_verified_at is not None,
            "mfa_enabled": user.has_confirmed_mfa(),
            "is_staff": user.is_staff,
        }
    ).data
