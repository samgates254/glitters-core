"""Server-stored rendered leases, versioned and restricted by visibility."""

from __future__ import annotations

import uuid

import pytest
from django.db import connection
from tests.integration.test_tenancy_api import (
    DOCUMENT,
    DOCUMENT_B64,
    LEASES,
    _client,
    _create,
    _member,
    _organization,
    _unit,
    _user,
)

from apps.tenancy.models import Lease, StoredLeaseDocument
from apps.tenancy.services.document import compute_rendered_document_hash

pytestmark = pytest.mark.django_db


def test_approval_stores_bytes_and_ignores_a_client_uri() -> None:
    owner = _user("owner")
    tenant = _user("tenant")
    stranger = _user("stranger")
    outsider = _user("outsider")
    prop = _unit(owner, "Nairobi synthetic document")
    other = _organization("Other PM")
    _member(outsider, other, role="admin")
    created = _create(owner, prop, tenant)
    assert created.status_code == 201
    lease_id = created.json()["id"]

    approved = _client(owner).post(
        f"{LEASES}{lease_id}/approve/",
        {
            "rendered_document_base64": DOCUMENT_B64,
            "rendered_document_uri": "https://evil.example/lease.pdf",
            "deadline_hours": 72,
        },
        format="json",
    )
    assert approved.status_code == 200
    digest = compute_rendered_document_hash(DOCUMENT)
    assert approved.json()["rendered_document_hash"] == digest
    stored = StoredLeaseDocument.objects.get(lease_id=lease_id)
    assert stored.document_version == 1
    assert stored.content_sha256 == digest
    assert stored.byte_size == len(DOCUMENT)
    assert bytes(stored.content) == DOCUMENT
    assert stored.media_type == "application/pdf"
    assert stored.locator == f"tenancy://leases/{lease_id}/documents/1"
    assert "evil.example" not in stored.locator

    metadata = _client(owner).get(f"{LEASES}{lease_id}/document/")
    assert metadata.status_code == 200
    body = metadata.json()
    assert body["stored"] is True
    assert body["byte_size"] == len(DOCUMENT)
    assert body["rendered_document_uri"] == stored.locator
    assert DOCUMENT_B64 not in str(body)
    assert "content" not in body

    anonymous = _client().get(f"{LEASES}{lease_id}/document/content/")
    assert anonymous.status_code == 401
    assert _client(stranger).get(f"{LEASES}{lease_id}/document/content/").status_code == 404
    assert _client(outsider).get(f"{LEASES}{lease_id}/document/content/").status_code == 404

    downloaded = _client(tenant).get(f"{LEASES}{lease_id}/document/content/")
    assert downloaded.status_code == 200
    assert downloaded.content == DOCUMENT
    assert downloaded["Content-Type"].startswith("application/pdf")
    assert downloaded["X-Content-SHA256"] == digest
    assert downloaded["Cache-Control"] == "private, no-store"
    assert b"ip_address" not in downloaded.content

    owner_copy = _client(owner).get(f"{LEASES}{lease_id}/document/content/")
    assert owner_copy.content == DOCUMENT
    detail = _client(owner).get(f"{LEASES}{lease_id}/").json()
    assert DOCUMENT not in str(detail).encode()

    with connection.cursor() as cursor:
        cursor.execute(
            "UPDATE tenancy_stored_lease_document SET content = %s WHERE id = %s",
            [b"%PDF-1.7\ntampered\n", stored.id],
        )
    tampered = _client(owner).get(f"{LEASES}{lease_id}/document/content/")
    assert tampered.status_code == 409
    assert tampered.content != DOCUMENT
    assert b"%PDF-1.7\ntampered" not in tampered.content
    lease = Lease.objects.get(pk=lease_id)
    assert lease.rendered_document_hash == digest
    assert lease.document_version == 1
    assert StoredLeaseDocument.objects.filter(lease=lease).count() == 1


def test_missing_document_content_stays_hidden() -> None:
    missing = uuid.uuid4()
    owner = _user("owner")
    assert _client().get(f"{LEASES}{missing}/document/content/").status_code == 401
    assert _client(owner).get(f"{LEASES}{missing}/document/content/").status_code == 404
