"""Domain failures mapped to HTTP at the view boundary."""

from __future__ import annotations

from rest_framework.exceptions import APIException


class IdentityError(Exception):
    def __init__(self, detail: str, *, status_code: int = 400):
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


class IdentityAPIException(APIException):
    status_code = 400
    default_detail = "Request could not be completed."


def raise_for_identity(exc: IdentityError) -> None:
    error = IdentityAPIException(detail=exc.detail)
    error.status_code = exc.status_code
    raise error from exc
