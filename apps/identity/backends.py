"""Authentication backend for identity.User.

Password checks still reject inactive accounts. Session loading returns the
user anyway so ActiveAccountMiddleware can explain a suspended or deactivated
session instead of pretending the cookie was never issued.
"""

from __future__ import annotations

from django.contrib.auth.backends import ModelBackend

from apps.identity.models import User


class IdentityBackend(ModelBackend):
    def get_user(self, user_id):
        try:
            return User.objects.get(pk=user_id)
        except User.DoesNotExist:
            return None
