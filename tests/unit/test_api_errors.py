from rest_framework.exceptions import ValidationError

from config.api import glitters_exception_handler


def test_exception_handler_includes_the_request_id() -> None:
    request = type("Request", (), {"request_id": "trace-123"})()

    response = glitters_exception_handler(
        ValidationError({"email": ["This field is required."]}),
        {"request": request},
    )

    assert response is not None
    assert response.status_code == 400
    assert response.data["request_id"] == "trace-123"
    assert "email" in response.data["errors"]
