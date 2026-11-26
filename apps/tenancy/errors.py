"""Domain failures for lease services. Views map these to HTTP responses."""

from __future__ import annotations

from rest_framework.exceptions import APIException


class TenancyError(Exception):
    def __init__(self, detail: str, *, status_code: int = 400):
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


class TenancyAPIException(APIException):
    status_code = 400
    default_detail = "Tenancy request could not be completed."


def raise_for_tenancy(exc: TenancyError) -> None:
    error = TenancyAPIException(detail=exc.detail)
    error.status_code = exc.status_code
    raise error from exc
