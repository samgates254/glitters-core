import pytest
from django.core.cache import cache
from django.test import override_settings
from rest_framework.test import APIClient

from apps.core.checks import check_gdal_available


def test_live_health_is_public(api_client: APIClient) -> None:
    response = api_client.get("/api/v1/health/live/", HTTP_X_REQUEST_ID="trace-123")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["service"] == "glitters-core"
    assert body["version"] == "0.1.0"
    assert body["request_id"] == "trace-123"
    assert response["X-Request-ID"] == "trace-123"


def test_unsafe_request_id_is_replaced(api_client: APIClient) -> None:
    response = api_client.get("/api/v1/health/live/", HTTP_X_REQUEST_ID="bad id")

    assert response.status_code == 200
    assert response["X-Request-ID"] != "bad id"
    assert response.json()["request_id"] == response["X-Request-ID"]


@pytest.mark.django_db
def test_ready_health_checks_database_postgis_and_redis(api_client: APIClient) -> None:
    response = api_client.get("/api/v1/health/ready/")

    assert response.status_code == 200
    checks = response.json()["checks"]
    assert checks["database"]["status"] == "ok"
    assert checks["redis"]["status"] == "ok"
    assert checks["postgis"]["status"] == "ok"
    assert checks["postgis"]["version"]


@pytest.mark.django_db
def test_redis_cache_round_trip() -> None:
    key = "glitters:test:ping"
    cache.set(key, "ok", timeout=30)
    try:
        assert cache.get(key) == "ok"
    finally:
        cache.delete(key)


def test_celery_broker_is_reachable() -> None:
    from config.celery import app

    connection = app.connection()
    connection.ensure_connection(max_retries=1)
    connection.release()


@pytest.mark.django_db
def test_ready_hides_dependency_failures(
    api_client: APIClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class BrokenCursor:
        def __enter__(self):
            raise RuntimeError("database password=secret")

        def __exit__(self, exc_type, exc, traceback):
            return False

    class BrokenRedis:
        def ping(self):
            raise RuntimeError("redis password=secret")

        def close(self):
            return None

    monkeypatch.setattr("apps.core.services.health.connection.cursor", lambda: BrokenCursor())
    monkeypatch.setattr(
        "apps.core.services.health.Redis.from_url",
        lambda *args, **kwargs: BrokenRedis(),
    )

    response = api_client.get("/api/v1/health/ready/")

    assert response.status_code == 503
    body = response.content.decode()
    assert "secret" not in body
    checks = response.json()["checks"]
    assert checks["database"] == {"status": "error", "detail": "unavailable"}
    assert checks["postgis"] == {"status": "error", "detail": "unavailable"}
    assert checks["redis"] == {"status": "error", "detail": "unavailable"}


@override_settings(GDAL_AVAILABLE=False)
def test_missing_gdal_emits_a_warning() -> None:
    issues = check_gdal_available(None)

    assert [issue.id for issue in issues] == ["glitters.W001"]


@override_settings(GDAL_AVAILABLE=True)
def test_present_gdal_is_silent() -> None:
    assert check_gdal_available(None) == []
