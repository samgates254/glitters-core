"""Server-held rendered lease documents.

The client supplies bytes at approval. The server chooses the locator,
stores the bytes, and serves them only to someone who can see the lease.
Retrieval recomputes SHA-256 and refuses a mismatch. This does not generate
a PDF/A and it is not a signature seal.
"""

from __future__ import annotations

from apps.tenancy.errors import TenancyError
from apps.tenancy.models import StoredLeaseDocument
from apps.tenancy.services.document import compute_rendered_document_hash

DOCUMENT_MISMATCH = "The stored lease document failed integrity verification."


def media_type_for(document: bytes) -> str:
    if document.startswith(b"%PDF-"):
        return "application/pdf"
    return "application/octet-stream"


def store_rendered_lease_document(*, actor, lease, document: bytes) -> StoredLeaseDocument:
    """Persist one version. A repeated identical store returns the same row."""

    digest = compute_rendered_document_hash(document)
    if digest != lease.rendered_document_hash:
        raise TenancyError(DOCUMENT_MISMATCH, status_code=409)
    existing = StoredLeaseDocument.objects.filter(
        lease=lease,
        document_version=lease.document_version,
    ).first()
    if existing is not None:
        same_hash = existing.content_sha256 == digest
        same_bytes = bytes(existing.content) == document
        if same_hash and same_bytes and existing.byte_size == len(document):
            return existing
        raise TenancyError(DOCUMENT_MISMATCH, status_code=409)
    row = StoredLeaseDocument(
        lease=lease,
        document_version=lease.document_version,
        content_sha256=digest,
        byte_size=len(document),
        media_type=media_type_for(document),
        content=document,
        created_by=actor,
    )
    row.save()
    return row


def verify_stored_lease_document(lease) -> StoredLeaseDocument:
    """Return the current version only when the stored bytes match both hashes."""

    row = (
        StoredLeaseDocument.objects.filter(
            lease=lease,
            document_version=lease.document_version,
        )
        .order_by("created_at")
        .first()
    )
    if row is None:
        raise TenancyError(DOCUMENT_MISMATCH, status_code=409)
    payload = bytes(row.content)
    digest = compute_rendered_document_hash(payload)
    matches = (
        digest == row.content_sha256
        and digest == lease.rendered_document_hash
        and row.byte_size == len(payload)
    )
    if not matches:
        raise TenancyError(DOCUMENT_MISMATCH, status_code=409)
    return row
