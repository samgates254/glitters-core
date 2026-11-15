"""File-content checks. The declared MIME type is not evidence."""

from __future__ import annotations

import io

from apps.marketplace.domain import DOCUMENT_MIME, MEDIA_SIGNATURE, PHOTOGRAPH_MIMES
from apps.marketplace.errors import MarketplaceError

_JPEG = b"\xff\xd8\xff"
_PNG = b"\x89PNG\r\n\x1a\n"


def inspect_upload(data: bytes, declared_mime: str) -> None:
    if not isinstance(data, bytes) or not isinstance(declared_mime, str):
        raise MarketplaceError(MEDIA_SIGNATURE, status_code=400)
    if declared_mime in PHOTOGRAPH_MIMES:
        detected = _image_mime(data)
    elif declared_mime == DOCUMENT_MIME:
        detected = DOCUMENT_MIME if _pdf_ok(data) else None
    else:
        detected = None
    if detected != declared_mime:
        raise MarketplaceError(MEDIA_SIGNATURE, status_code=400)


def _image_mime(data: bytes) -> str | None:
    if data.startswith(_JPEG):
        expected, pillow_format = "image/jpeg", "JPEG"
    elif data.startswith(_PNG):
        expected, pillow_format = "image/png", "PNG"
    elif len(data) >= 12 and data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        expected, pillow_format = "image/webp", "WEBP"
    else:
        return None
    if _decoded_format(data) != pillow_format:
        return None
    return expected


def _decoded_format(data: bytes) -> str | None:
    from PIL import Image, UnidentifiedImageError

    try:
        with Image.open(io.BytesIO(data)) as image:
            image.load()
            return image.format
    except UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError:
        return None


def _pdf_ok(data: bytes) -> bool:
    if len(data) < 32 or not data.startswith(b"%PDF-"):
        return False
    if b"%%EOF" not in data:
        return False
    _header, separator, body = data.partition(b"\n")
    if separator != b"\n" or b"obj" not in body:
        return False
    return True
