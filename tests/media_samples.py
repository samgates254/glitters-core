"""Generated image and PDF bytes for media tests. This module is not a test module."""

from __future__ import annotations

import io

from PIL import Image

from apps.marketplace.storage import get_object_storage

_UPLOAD_PREFIX = "memory://upload/"


def jpeg_bytes() -> bytes:
    return _image("JPEG")


def png_bytes() -> bytes:
    return _image("PNG")


def webp_bytes() -> bytes:
    return _image("WEBP")


def pdf_bytes() -> bytes:
    return b"%PDF-1.1\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF\n"


def header_only_pdf() -> bytes:
    return b"%PDF-1.7\n%%EOF"


def store_upload(session: dict, data: bytes) -> str:
    url = session["upload"]["url"]
    if not url.startswith(_UPLOAD_PREFIX):
        raise AssertionError("the test upload target must be the memory adapter")
    key = url.removeprefix(_UPLOAD_PREFIX)
    content_type = session["upload"]["headers"]["Content-Type"]
    get_object_storage().put_bytes(key, data, content_type=content_type)
    return key


def _image(fmt: str) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (2, 2), (20, 40, 60)).save(buffer, format=fmt)
    return buffer.getvalue()
