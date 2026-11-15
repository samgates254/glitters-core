"""Domain errors and HTTP exception mapping for operations."""

from __future__ import annotations

from rest_framework.exceptions import APIException


class OperationsError(Exception):
    def __init__(self, detail: str, *, status_code: int = 400):
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


class OperationsAPIException(APIException):
    status_code = 400
    default_detail = "Operations request could not be completed."


def raise_for_operations(exc: OperationsError) -> None:
    error = OperationsAPIException(detail=exc.detail)
    error.status_code = exc.status_code
    raise error from exc
