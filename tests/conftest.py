import pytest
from rest_framework.test import APIClient

PASSWORD = "Night-Bridge-Kestrel-42"


@pytest.fixture
def api_client() -> APIClient:
    return APIClient()


@pytest.fixture
def password() -> str:
    return PASSWORD


@pytest.fixture(autouse=True)
def _disable_identity_throttle_in_tests(settings):
    """Keep the suite deterministic. Throttle behavior has its own test."""

    settings.IDENTITY_THROTTLE = False
    settings.ENQUIRY_THROTTLE = False


@pytest.fixture(autouse=True)
def _reset_media_storage():
    from apps.marketplace.storage import reset_object_storage

    reset_object_storage()
    yield
    reset_object_storage()


@pytest.fixture
def locmem_email(settings):
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
