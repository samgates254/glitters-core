"""RFC 6238 TOTP with the standard 30-second SHA-1 step.

The algorithm is SHA-1 because authenticator applications expect that default.
The shared secret is what must stay confidential.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import struct
import time
from urllib.parse import quote

STEP_SECONDS = 30
DIGITS = 6


def random_secret() -> bytes:
    import secrets

    return secrets.token_bytes(20)


def encode_secret(secret: bytes) -> str:
    return base64.b32encode(secret).decode("ascii").rstrip("=")


def decode_secret(encoded: str) -> bytes:
    padding = "=" * (-len(encoded) % 8)
    return base64.b32decode(encoded + padding, casefold=True)


def otpauth_uri(*, secret: bytes, account: str, issuer: str = "GLITTERS") -> str:
    label = quote(f"{issuer}:{account}")
    secret_text = encode_secret(secret)
    return (
        f"otpauth://totp/{label}?secret={secret_text}&issuer={quote(issuer)}"
        "&algorithm=SHA1&digits=6&period=30"
    )


def totp_at(secret: bytes, moment: float | None = None) -> str:
    counter = int((time.time() if moment is None else moment) // STEP_SECONDS)
    message = struct.pack(">Q", counter)
    digest = hmac.new(secret, message, hashlib.sha1).digest()  # noqa: S324
    offset = digest[-1] & 0x0F
    binary = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(binary % (10**DIGITS)).zfill(DIGITS)


def verify_totp(secret: bytes, code: str, *, window: int = 1, moment: float | None = None) -> bool:
    cleaned = str(code or "").strip()
    if len(cleaned) != DIGITS or not cleaned.isdigit():
        return False
    current = time.time() if moment is None else moment
    for offset in range(-window, window + 1):
        candidate = totp_at(secret, current + (offset * STEP_SECONDS))
        if hmac.compare_digest(candidate, cleaned):
            return True
    return False
