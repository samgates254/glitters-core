"""Private object storage. Provider clients stay behind this adapter.

Presigned URLs are returned to the caller and are never stored.
"""

from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass
from typing import Protocol

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

logger = logging.getLogger(__name__)


class StorageUnavailable(Exception):
    """The provider could not complete the request. The message stays generic."""


class StorageObjectMissing(Exception):
    """The object key is not in the bucket."""


@dataclass(frozen=True)
class StoredObject:
    size: int
    content_type: str


class ObjectStorage(Protocol):
    provider: str
    bucket: str

    def presign_put(
        self,
        *,
        key: str,
        content_type: str,
        content_length: int,
        expires_in: int,
    ) -> str: ...

    def presign_get(self, *, key: str, expires_in: int) -> str: ...

    def head(self, key: str) -> StoredObject | None: ...

    def get_bytes(self, key: str, *, maximum: int) -> bytes: ...

    def delete(self, key: str) -> None: ...


class MemoryStorage:
    """Process-local bucket for development and tests. Signed GET URLs are opaque."""

    provider = "memory"
    bucket = "memory"

    def __init__(self) -> None:
        self._objects: dict[str, tuple[bytes, str]] = {}

    def presign_put(
        self,
        *,
        key: str,
        content_type: str,
        content_length: int,
        expires_in: int,
    ) -> str:
        _require_key(key)
        if expires_in < 1 or content_length < 1 or not content_type:
            raise StorageUnavailable
        return f"memory://upload/{key}"

    def presign_get(self, *, key: str, expires_in: int) -> str:
        _require_key(key)
        if expires_in < 1:
            raise StorageUnavailable
        token = secrets.token_urlsafe(32)
        return f"https://media.invalid/signed/{token}"

    def put_bytes(self, key: str, data: bytes, content_type: str) -> None:
        _require_key(key)
        self._objects[key] = (bytes(data), content_type)

    def head(self, key: str) -> StoredObject | None:
        _require_key(key)
        row = self._objects.get(key)
        if row is None:
            return None
        return StoredObject(size=len(row[0]), content_type=row[1])

    def get_bytes(self, key: str, *, maximum: int) -> bytes:
        _require_key(key)
        row = self._objects.get(key)
        if row is None:
            raise StorageObjectMissing
        if maximum < 0:
            raise StorageUnavailable
        return row[0][: maximum + 1]

    def delete(self, key: str) -> None:
        _require_key(key)
        self._objects.pop(key, None)


class R2Storage:
    """Cloudflare R2 through the S3 API. The bucket stays private."""

    provider = "r2"

    def __init__(
        self,
        *,
        endpoint: str,
        access_key: str,
        secret_key: str,
        bucket: str,
        region: str,
    ) -> None:
        self.bucket = bucket
        self._endpoint = endpoint
        self._access_key = access_key
        self._secret_key = secret_key
        self._region = region or "auto"
        self._client = None

    def __repr__(self) -> str:
        return f"R2Storage(bucket={self.bucket!r})"

    def presign_put(
        self,
        *,
        key: str,
        content_type: str,
        content_length: int,
        expires_in: int,
    ) -> str:
        _require_key(key)
        if expires_in < 1 or content_length < 1 or not content_type:
            raise StorageUnavailable
        return self._client_call(
            "generate_presigned_url",
            "put_object",
            Params={
                "Bucket": self.bucket,
                "Key": key,
                "ContentType": content_type,
                "ContentLength": content_length,
            },
            ExpiresIn=expires_in,
            HttpMethod="PUT",
        )

    def presign_get(self, *, key: str, expires_in: int) -> str:
        _require_key(key)
        if expires_in < 1:
            raise StorageUnavailable
        return self._client_call(
            "generate_presigned_url",
            "get_object",
            Params={"Bucket": self.bucket, "Key": key},
            ExpiresIn=expires_in,
            HttpMethod="GET",
        )

    def head(self, key: str) -> StoredObject | None:
        _require_key(key)
        try:
            response = self._client_call("head_object", Bucket=self.bucket, Key=key)
        except StorageObjectMissing:
            return None
        return StoredObject(
            size=int(response["ContentLength"]),
            content_type=str(response.get("ContentType", "")),
        )

    def get_bytes(self, key: str, *, maximum: int) -> bytes:
        _require_key(key)
        if maximum < 0:
            raise StorageUnavailable
        response = self._client_call(
            "get_object",
            Bucket=self.bucket,
            Key=key,
            Range=f"bytes=0-{maximum}",
        )
        return response["Body"].read(maximum + 1)

    def delete(self, key: str) -> None:
        _require_key(key)
        try:
            self._client_call("delete_object", Bucket=self.bucket, Key=key)
        except StorageObjectMissing:
            return

    def _s3(self):
        if self._client is None:
            import boto3
            from botocore.config import Config

            self._client = boto3.client(
                "s3",
                endpoint_url=self._endpoint,
                aws_access_key_id=self._access_key,
                aws_secret_access_key=self._secret_key,
                region_name=self._region,
                config=Config(
                    signature_version="s3v4",
                    s3={"addressing_style": "path"},
                    retries={"max_attempts": 2, "mode": "standard"},
                ),
            )
        return self._client

    def _client_call(self, method: str, *args, **kwargs):
        from botocore.exceptions import BotoCoreError, ClientError

        try:
            return getattr(self._s3(), method)(*args, **kwargs)
        except ClientError as exc:
            code = str(exc.response.get("Error", {}).get("Code", ""))
            status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
            if code in {"404", "NoSuchKey", "NotFound"} or status == 404:
                raise StorageObjectMissing from None
            logger.warning("object storage request failed (%s)", code or "unavailable")
            raise StorageUnavailable from None
        except BotoCoreError:
            logger.warning("object storage request failed (unavailable)")
            raise StorageUnavailable from None


def _require_key(key: str) -> None:
    if (
        not isinstance(key, str)
        or not key
        or key.startswith("/")
        or ".." in key
        or "\\" in key
        or "://" in key
        or "\x00" in key
        or len(key) > 500
    ):
        raise StorageUnavailable


_cached: ObjectStorage | None = None
_cache_key: tuple | None = None


def get_object_storage() -> ObjectStorage:
    global _cached, _cache_key
    backend = settings.MEDIA_STORAGE_BACKEND
    key = (
        backend,
        settings.R2_ENDPOINT_URL,
        settings.R2_ACCESS_KEY_ID,
        settings.R2_SECRET_ACCESS_KEY,
        settings.R2_BUCKET,
        settings.R2_REGION,
    )
    if _cached is not None and _cache_key == key:
        return _cached
    _cached = _build(backend)
    _cache_key = key
    return _cached


def reset_object_storage() -> None:
    global _cached, _cache_key
    _cached = None
    _cache_key = None


def _build(backend: str) -> ObjectStorage:
    if backend == "memory":
        return MemoryStorage()
    if backend == "r2":
        endpoint = settings.R2_ENDPOINT_URL
        access_key = settings.R2_ACCESS_KEY_ID
        secret_key = settings.R2_SECRET_ACCESS_KEY
        bucket = settings.R2_BUCKET
        configured = all(
            isinstance(item, str) and item.strip()
            for item in (endpoint, access_key, secret_key, bucket)
        )
        if not configured:
            raise ImproperlyConfigured("Object storage is not configured.")
        return R2Storage(
            endpoint=endpoint,
            access_key=access_key,
            secret_key=secret_key,
            bucket=bucket,
            region=settings.R2_REGION or "auto",
        )
    raise ImproperlyConfigured("Object storage is not configured.")
