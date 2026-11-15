"""HTTP adapters for identity services."""

from __future__ import annotations

from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_protect, ensure_csrf_cookie
from drf_spectacular.utils import extend_schema
from rest_framework.authentication import SessionAuthentication
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.identity.domain import (
    AUTHENTICATION_REQUIRED,
    GENERIC_REGISTER,
    GENERIC_RESEND,
    GENERIC_RESET,
)
from apps.identity.errors import IdentityError, raise_for_identity
from apps.identity.models import TokenPurpose
from apps.identity.serializers import (
    CodeSerializer,
    EmailSerializer,
    LoginSerializer,
    MessageSerializer,
    MfaConfirmSerializer,
    MfaDisableSerializer,
    MfaEnrollSerializer,
    MfaVerifySerializer,
    PasswordChangeSerializer,
    PasswordResetConfirmSerializer,
    PasswordSerializer,
    PhoneSerializer,
    ProfileSerializer,
    RefreshSerializer,
    RegisterSerializer,
    RevokeSerializer,
    TokenSerializer,
    UserPublicSerializer,
    public_user,
)
from apps.identity.services.accounts import deactivate_account, register_account, update_profile
from apps.identity.services.audit import record_event
from apps.identity.services.authentication import (
    complete_enrollment,
    complete_mfa,
    login_user,
)
from apps.identity.services.credentials import (
    end_browser_session,
    revoke_access_token,
    revoke_browser_sessions,
    revoke_mobile_credentials,
    rotate_refresh_token,
)
from apps.identity.services.mfa import begin_enrollment, confirm_device, disable_mfa
from apps.identity.services.passwords import (
    change_password,
    confirm_password_reset,
    request_password_reset,
)
from apps.identity.services.tokens import find_live_token
from apps.identity.services.verification import (
    resend_email_verification,
    set_phone,
    verify_email,
    verify_phone,
)
from apps.identity.throttles import (
    LoginThrottle,
    MfaThrottle,
    PasswordResetThrottle,
    PhoneThrottle,
    RegisterThrottle,
    VerifyThrottle,
)


def _call(func, *args, **kwargs):
    try:
        return func(*args, **kwargs)
    except IdentityError as exc:
        raise_for_identity(exc)


@method_decorator(csrf_protect, name="dispatch")
class IdentityAPIView(APIView):
    """DRF marks handlers CSRF-exempt. These views opt back into the check."""


def _login_body(result) -> dict:
    if result.kind == "mfa":
        return {"mfa_required": True, "challenge_token": result.challenge_token}
    if result.kind == "enroll":
        return {
            "mfa_enrollment_required": True,
            "enrollment_token": result.enrollment_token,
        }
    body = {"user": public_user(result.user)}
    if result.credential is not None:
        body["access_token"] = result.credential.access_token
        body["refresh_token"] = result.credential.refresh_token
        body["expires_in"] = result.credential.expires_in
    if result.recovery_codes:
        body["recovery_codes"] = result.recovery_codes
    return body


class CsrfView(IdentityAPIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = []

    @extend_schema(responses={200: MessageSerializer})
    @method_decorator(ensure_csrf_cookie)
    def get(self, request):
        return Response({"detail": "ok"})


class RegisterView(IdentityAPIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [RegisterThrottle]

    @extend_schema(request=RegisterSerializer, responses={202: MessageSerializer})
    def post(self, request):
        serializer = RegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        _call(
            register_account,
            email=data["email"],
            password=data["password"],
            first_name=data.get("first_name", ""),
            last_name=data.get("last_name", ""),
            phone=data.get("phone_e164", ""),
            request=request,
        )
        return Response({"detail": GENERIC_REGISTER}, status=202)


class LoginView(IdentityAPIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [LoginThrottle]

    @extend_schema(request=LoginSerializer, responses={200: UserPublicSerializer})
    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        result = _call(login_user, request=request, **serializer.validated_data)
        return Response(_login_body(result))


class LogoutView(IdentityAPIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(request=None, responses={200: MessageSerializer})
    def post(self, request):
        header = request.headers.get("Authorization", "")
        if header.startswith("Bearer "):
            revoke_access_token(header[7:].strip())
            record_event(request.user, "logout", request)
        if request.session.get("_auth_user_id"):
            end_browser_session(request)
        return Response({"detail": "Signed out."})


class MfaVerifyView(IdentityAPIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [MfaThrottle]

    @extend_schema(request=MfaVerifySerializer, responses={200: UserPublicSerializer})
    def post(self, request):
        serializer = MfaVerifySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        result = _call(complete_mfa, request=request, **serializer.validated_data)
        return Response(_login_body(result))


class MfaEnrollView(IdentityAPIView):
    authentication_classes = [SessionAuthentication]
    permission_classes = [AllowAny]
    throttle_classes = [MfaThrottle]

    @extend_schema(request=MfaEnrollSerializer, responses={200: MessageSerializer})
    def post(self, request):
        serializer = MfaEnrollSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        token = serializer.validated_data.get("enrollment_token") or ""
        if token:
            identity_token = _call(find_live_token, token, TokenPurpose.MFA_ENROLLMENT)
            user = identity_token.user
        else:
            user = _session_user(request)
        if user is None:
            raise_for_identity(IdentityError(AUTHENTICATION_REQUIRED, status_code=401))
        payload = _call(begin_enrollment, user)
        record_event(user, "mfa_enrolled", request)
        return Response(payload)


class MfaConfirmView(IdentityAPIView):
    authentication_classes = [SessionAuthentication]
    permission_classes = [AllowAny]
    throttle_classes = [MfaThrottle]

    @extend_schema(request=MfaConfirmSerializer, responses={200: UserPublicSerializer})
    def post(self, request):
        serializer = MfaConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        token = serializer.validated_data.get("enrollment_token") or ""
        code = serializer.validated_data["code"]
        if token:
            result = _call(
                complete_enrollment,
                enrollment_token=token,
                code=code,
                request=request,
            )
            return Response(_login_body(result))
        user = _session_user(request)
        if user is None:
            raise_for_identity(IdentityError(AUTHENTICATION_REQUIRED, status_code=401))
        codes = _call(confirm_device, user, code)
        record_event(user, "mfa_confirmed", request)
        return Response({"recovery_codes": codes, "user": public_user(user)})


class MfaDisableView(IdentityAPIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [MfaThrottle]

    @extend_schema(request=MfaDisableSerializer, responses={200: MessageSerializer})
    def post(self, request):
        serializer = MfaDisableSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        _call(
            disable_mfa,
            request.user,
            password=serializer.validated_data["password"],
            code=serializer.validated_data["code"],
        )
        record_event(request.user, "mfa_disabled", request)
        return Response({"detail": "Multi-factor authentication is disabled."})


class ProfileView(IdentityAPIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(responses={200: UserPublicSerializer})
    def get(self, request):
        return Response(public_user(request.user))

    @extend_schema(request=ProfileSerializer, responses={200: UserPublicSerializer})
    def patch(self, request):
        serializer = ProfileSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        user = _call(
            update_profile,
            request.user,
            first_name=data.get("first_name", request.user.first_name),
            last_name=data.get("last_name", request.user.last_name),
        )
        return Response(public_user(user))


class EmailVerifyView(IdentityAPIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [VerifyThrottle]

    @extend_schema(request=TokenSerializer, responses={200: UserPublicSerializer})
    def post(self, request):
        serializer = TokenSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = _call(verify_email, serializer.validated_data["token"], request)
        return Response(public_user(user))


class EmailResendView(IdentityAPIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [VerifyThrottle]

    @extend_schema(request=EmailSerializer, responses={202: MessageSerializer})
    def post(self, request):
        serializer = EmailSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        _call(resend_email_verification, serializer.validated_data["email"], request)
        return Response({"detail": GENERIC_RESEND}, status=202)


class PasswordResetView(IdentityAPIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [PasswordResetThrottle]

    @extend_schema(request=EmailSerializer, responses={202: MessageSerializer})
    def post(self, request):
        serializer = EmailSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        _call(request_password_reset, serializer.validated_data["email"], request)
        return Response({"detail": GENERIC_RESET}, status=202)


class PasswordResetConfirmView(IdentityAPIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [PasswordResetThrottle]

    @extend_schema(request=PasswordResetConfirmSerializer, responses={200: MessageSerializer})
    def post(self, request):
        serializer = PasswordResetConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        _call(confirm_password_reset, request=request, **serializer.validated_data)
        return Response({"detail": "Password updated."})


class PasswordChangeView(IdentityAPIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(request=PasswordChangeSerializer, responses={200: MessageSerializer})
    def post(self, request):
        serializer = PasswordChangeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        _call(change_password, request.user, request=request, **serializer.validated_data)
        return Response({"detail": "Password updated."})


class RefreshView(IdentityAPIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [LoginThrottle]

    @extend_schema(request=RefreshSerializer, responses={200: MessageSerializer})
    def post(self, request):
        serializer = RefreshSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        issued = _call(rotate_refresh_token, serializer.validated_data["refresh_token"], request)
        return Response(
            {
                "access_token": issued.access_token,
                "refresh_token": issued.refresh_token,
                "expires_in": issued.expires_in,
            }
        )


class RevokeView(IdentityAPIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(request=RevokeSerializer, responses={200: MessageSerializer})
    def post(self, request):
        serializer = RevokeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        scope = serializer.validated_data["scope"]
        user = request.user
        keep = None if scope == "all" else request.session.session_key
        revoke_mobile_credentials(user)
        revoke_browser_sessions(user, keep_session_key=keep)
        record_event(user, "sessions_revoked", request)
        if scope == "all":
            end_browser_session(request)
        return Response({"detail": "Sessions revoked."})


class PhoneView(IdentityAPIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [PhoneThrottle]

    @extend_schema(request=PhoneSerializer, responses={202: MessageSerializer})
    def post(self, request):
        serializer = PhoneSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        _call(set_phone, request.user, serializer.validated_data["phone_e164"], request)
        return Response({"detail": "If the number can be verified, we sent a code."}, status=202)


class PhoneVerifyView(IdentityAPIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [PhoneThrottle]

    @extend_schema(request=CodeSerializer, responses={200: UserPublicSerializer})
    def post(self, request):
        serializer = CodeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        _call(verify_phone, request.user, serializer.validated_data["code"], request)
        request.user.refresh_from_db()
        return Response(public_user(request.user))


class DeactivateView(IdentityAPIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(request=PasswordSerializer, responses={200: MessageSerializer})
    def post(self, request):
        serializer = PasswordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        _call(deactivate_account, request.user, serializer.validated_data["password"], request)
        return Response({"detail": "Account deactivated."})


def _session_user(request):
    user = getattr(request, "user", None)
    if getattr(user, "is_authenticated", False):
        return user
    return None
