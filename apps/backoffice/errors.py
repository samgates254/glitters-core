from rest_framework.exceptions import APIException


class BackofficeError(Exception):
    def __init__(self, detail: str, *, status_code: int = 400):
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


class BackofficeAPIException(APIException):
    status_code = 400
    default_detail = "Backoffice request could not be completed."
    default_code = "backoffice_error"


def raise_for_backoffice(exc: BackofficeError) -> None:
    error = BackofficeAPIException(detail=exc.detail)
    error.status_code = exc.status_code
    raise error from exc
