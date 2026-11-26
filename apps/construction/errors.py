from rest_framework.exceptions import APIException


class ConstructionError(Exception):
    def __init__(self, detail: str, *, status_code: int = 400):
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


class ConstructionAPIException(APIException):
    status_code = 400
    default_detail = "Construction request could not be completed."


def raise_for_construction(exc: ConstructionError) -> None:
    error = ConstructionAPIException(detail=exc.detail)
    error.status_code = exc.status_code
    raise error from exc
