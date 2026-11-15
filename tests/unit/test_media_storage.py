"""Storage adapter and file-signature checks. No network and no R2 credentials."""

from unittest.mock import MagicMock, patch

import pytest
from django.core.exceptions import ImproperlyConfigured
from tests.media_samples import header_only_pdf, jpeg_bytes, pdf_bytes, png_bytes

from apps.marketplace.domain import clean_metadata
from apps.marketplace.errors import MarketplaceError
from apps.marketplace.services.content import inspect_upload
from apps.marketplace.storage import (
    R2Storage,
    get_object_storage,
    reset_object_storage,
)


def test_memory_download_url_does_not_contain_the_object_key() -> None:
    storage = get_object_storage()
    key = "properties/4c318845-5525-4966-ba65-efde611e7bbd/media/m/original"
    url = storage.presign_get(key=key, expires_in=30)
    assert url.startswith("https://media.invalid/signed/")
    assert key not in url
    assert "4c318845-5525-4966-ba65-efde611e7bbd" not in url


def test_r2_backend_without_credentials_fails_closed(settings) -> None:
    settings.MEDIA_STORAGE_BACKEND = "r2"
    settings.R2_ENDPOINT_URL = ""
    settings.R2_ACCESS_KEY_ID = ""
    settings.R2_SECRET_ACCESS_KEY = "super-secret-value"
    settings.R2_BUCKET = ""
    reset_object_storage()
    with pytest.raises(ImproperlyConfigured) as caught:
        get_object_storage()
    assert str(caught.value) == "Object storage is not configured."
    assert "super-secret-value" not in str(caught.value)


def test_r2_presign_put_stays_private(settings) -> None:
    settings.MEDIA_STORAGE_BACKEND = "r2"
    settings.R2_ENDPOINT_URL = "https://example.r2.cloudflarestorage.com"
    settings.R2_ACCESS_KEY_ID = "access-key"
    settings.R2_SECRET_ACCESS_KEY = "secret-key"
    settings.R2_BUCKET = "glitters-private"
    settings.R2_REGION = "auto"
    reset_object_storage()
    fake = MagicMock()
    fake.generate_presigned_url.return_value = "https://example.r2.cloudflarestorage.com/signed"
    with patch("boto3.client", return_value=fake):
        url = get_object_storage().presign_put(
            key="properties/p/media/m/original",
            content_type="image/jpeg",
            content_length=12,
            expires_in=60,
        )
    assert "secret-key" not in url
    args, kwargs = fake.generate_presigned_url.call_args
    assert args == ("put_object",)
    assert kwargs["Params"] == {
        "Bucket": "glitters-private",
        "Key": "properties/p/media/m/original",
        "ContentType": "image/jpeg",
        "ContentLength": 12,
    }
    assert kwargs["HttpMethod"] == "PUT"
    assert "ACL" not in kwargs["Params"]


def test_r2_missing_object_is_not_an_outage() -> None:
    from botocore.exceptions import ClientError

    storage = R2Storage(
        endpoint="https://example.r2.cloudflarestorage.com",
        access_key="access-key",
        secret_key="secret-key",
        bucket="glitters-private",
        region="auto",
    )

    class Missing:
        def head_object(self, **kwargs):
            raise ClientError(
                {"Error": {"Code": "404"}, "ResponseMetadata": {"HTTPStatusCode": 404}},
                "HeadObject",
            )

    storage._client = Missing()
    assert storage.head("properties/p/media/m/original") is None


def test_content_checks_use_bytes_not_the_declared_type() -> None:
    inspect_upload(jpeg_bytes(), "image/jpeg")
    inspect_upload(png_bytes(), "image/png")
    inspect_upload(pdf_bytes(), "application/pdf")
    with pytest.raises(MarketplaceError, match="not a supported"):
        inspect_upload(jpeg_bytes(), "image/png")
    with pytest.raises(MarketplaceError, match="not a supported"):
        inspect_upload(header_only_pdf(), "application/pdf")
    with pytest.raises(MarketplaceError, match="not a supported"):
        inspect_upload(b"GIF89a" + b"\x00" * 20, "image/jpeg")


def test_audit_metadata_drops_presigned_urls() -> None:
    cleaned = clean_metadata(
        {
            "kind": "document",
            "upload_url": "https://example.r2.cloudflarestorage.com/signed?X-Amz-Signature=abc",
            "storage_key": "properties/p/media/m/original",
            "secret_access_key": "secret-key",
        }
    )
    assert cleaned == {"kind": "document"}
