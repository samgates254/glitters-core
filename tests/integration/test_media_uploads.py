"""Upload sessions, private delivery, and public photograph visibility."""

from datetime import timedelta

import pytest
from django.conf import settings
from django.utils import timezone
from rest_framework.test import APIClient
from tests.identity_support import error_detail
from tests.marketplace_support import (
    LISTINGS,
    MANDATES,
    MODERATION,
    PUBLISHER,
    active_property,
    add_document,
    add_photo,
    attach,
    draft_listing,
    expect,
    self_mandate,
    staff_user,
)
from tests.media_samples import (
    header_only_pdf,
    jpeg_bytes,
    pdf_bytes,
    png_bytes,
    store_upload,
    webp_bytes,
)
from tests.organizations_support import client_for, verified_user

from apps.identity.models import User
from apps.marketplace.models import ListingEvent, MediaUploadSession, PropertyMedia
from apps.marketplace.storage import get_object_storage

pytestmark = pytest.mark.django_db


def _sessions(property_id: str) -> str:
    return f"/api/v1/marketplace/properties/{property_id}/media/upload-sessions/"


def _complete(session_id: str) -> str:
    return f"/api/v1/marketplace/media/upload-sessions/{session_id}/complete/"


def _open(client, property_id: str, *, kind: str, mime: str, size: int, **extra) -> dict:
    body = {"kind": kind, "mime_type": mime, "size_bytes": size, **extra}
    return expect(client.post(_sessions(property_id), body, format="json"), 201)


def test_unauthorized_callers_cannot_open_an_upload(password) -> None:
    owner = verified_user("ada@example.com", password)
    stranger = verified_user("stranger@example.com", password)
    unverified = User.objects.create_user("new@example.com", password)
    record = active_property(client_for(owner), name="Closed gate")
    body = {"kind": "photograph", "mime_type": "image/jpeg", "size_bytes": 32}
    anonymous = APIClient().post(_sessions(record["id"]), body, format="json")
    assert anonymous.status_code == 401
    hidden = client_for(stranger).post(_sessions(record["id"]), body, format="json")
    assert hidden.status_code == 404
    email = client_for(unverified).post(_sessions(record["id"]), body, format="json")
    assert email.status_code == 403
    assert error_detail(email) == "Verify your email before continuing."


def test_upload_rejects_bad_declarations_before_a_url_is_issued(password) -> None:
    owner = verified_user("ada@example.com", password)
    client = client_for(owner)
    record = active_property(client, name="Declared")
    gif = client.post(
        _sessions(record["id"]),
        {"kind": "photograph", "mime_type": "image/gif", "size_bytes": 32},
        format="json",
    )
    assert gif.status_code == 400
    assert b"memory://" not in gif.content
    flagged = client.post(
        _sessions(record["id"]),
        {
            "kind": "photograph",
            "mime_type": "image/jpeg",
            "size_bytes": True,
        },
        format="json",
    )
    assert flagged.status_code == 400
    oversized = client.post(
        _sessions(record["id"]),
        {
            "kind": "photograph",
            "mime_type": "image/jpeg",
            "size_bytes": settings.MEDIA_MAX_IMAGE_BYTES + 1,
        },
        format="json",
    )
    assert oversized.status_code == 400
    assert error_detail(oversized) == "This file is larger than the allowed size."
    assert MediaUploadSession.objects.count() == 0


def test_completion_verifies_bytes_and_is_idempotent(password) -> None:
    owner = verified_user("ada@example.com", password)
    other = verified_user("other@example.com", password)
    client = client_for(owner)
    record = active_property(client, name="Verified")
    data = jpeg_bytes()
    opened = _open(
        client,
        record["id"],
        kind="photograph",
        mime="image/jpeg",
        size=len(data),
        caption="Front elevation",
    )
    assert opened["upload"]["method"] == "PUT"
    assert opened["upload"]["headers"]["Content-Type"] == "image/jpeg"
    session = MediaUploadSession.objects.get(pk=opened["id"])
    assert session.status == "created"
    assert "memory://" not in session.storage_key
    assert opened["upload"]["url"] not in session.storage_key
    assert session.storage_key == f"properties/{record['id']}/media/{opened['media_id']}/original"
    ignored = client.post(_complete(opened["id"]), {"upload_status": "stored"}, format="json")
    assert ignored.status_code == 400
    assert error_detail(ignored) == "The uploaded file was not found."
    assert MediaUploadSession.objects.get(pk=opened["id"]).status == "rejected"

    reopened = _open(client, record["id"], kind="photograph", mime="image/jpeg", size=len(data))
    store_upload(reopened, data + b"zz")
    mismatch = client.post(_complete(reopened["id"]), format="json")
    assert mismatch.status_code == 400
    assert error_detail(mismatch) == "The uploaded file size does not match."

    forged = _open(client, record["id"], kind="photograph", mime="image/jpeg", size=32)
    store_upload(forged, b"x" * 32)
    signature = client.post(_complete(forged["id"]), format="json")
    assert signature.status_code == 400
    assert error_detail(signature) == "The uploaded file is not a supported image or document."

    broken = header_only_pdf()
    pdf_session = _open(
        client,
        record["id"],
        kind="document",
        mime="application/pdf",
        size=len(broken),
    )
    store_upload(pdf_session, broken)
    bad_pdf = client.post(_complete(pdf_session["id"]), format="json")
    assert bad_pdf.status_code == 400
    assert error_detail(bad_pdf) == "The uploaded file is not a supported image or document."

    stored = _open(client, record["id"], kind="photograph", mime="image/jpeg", size=len(data))
    key = store_upload(stored, data)
    stranger = client_for(other).post(_complete(stored["id"]), format="json")
    assert stranger.status_code == 404
    completed = expect(client.post(_complete(stored["id"]), format="json"), 200)
    assert completed["upload_status"] == "stored"
    assert completed["id"] == stored["media_id"]
    media = PropertyMedia.objects.get(pk=completed["id"])
    assert media.verified_at is not None
    assert media.file_size_bytes == len(data)
    assert len(media.checksum_sha256) == 64
    verified = ListingEvent.objects.filter(action="media.verified", object_id=str(media.id))
    assert verified.count() == 1
    again = expect(client.post(_complete(stored["id"]), format="json"), 200)
    assert again["id"] == completed["id"]
    assert verified.count() == 1
    get_object_storage().delete(key)
    assert get_object_storage().head(key) is None
    repeated = expect(client.post(_complete(stored["id"]), format="json"), 200)
    assert repeated["upload_status"] == "stored"
    started = ListingEvent.objects.filter(action="media.upload_started").first()
    assert "memory://" not in str(started.metadata)


@pytest.mark.parametrize(
    ("kind", "mime", "payload"),
    [
        ("photograph", "image/png", png_bytes),
        ("photograph", "image/webp", webp_bytes),
        ("document", "application/pdf", pdf_bytes),
    ],
)
def test_supported_formats_can_be_stored(password, kind, mime, payload) -> None:
    owner = verified_user("ada@example.com", password)
    client = client_for(owner)
    record = active_property(client, name=f"Format {kind} {mime.split('/')[-1]}")
    data = payload()
    opened = _open(client, record["id"], kind=kind, mime=mime, size=len(data))
    store_upload(opened, data)
    stored = expect(client.post(_complete(opened["id"]), format="json"), 200)
    assert stored["mime_type"] == mime
    assert stored["upload_status"] == "stored"
    if kind == "document":
        assert stored["visibility"] == "private"


def test_expired_session_cannot_be_completed(password) -> None:
    owner = verified_user("ada@example.com", password)
    client = client_for(owner)
    record = active_property(client, name="Expired upload")
    data = jpeg_bytes()
    opened = _open(client, record["id"], kind="photograph", mime="image/jpeg", size=len(data))
    store_upload(opened, data)
    session = MediaUploadSession.objects.get(pk=opened["id"])
    session.expires_at = timezone.now() - timedelta(minutes=1)
    session.save(update_fields=["expires_at", "updated_at"])
    expired = client.post(_complete(opened["id"]), format="json")
    assert expired.status_code == 409
    assert error_detail(expired) == "This upload session has expired."
    assert MediaUploadSession.objects.get(pk=opened["id"]).status == "expired"
    assert PropertyMedia.objects.get(pk=opened["media_id"]).upload_status == "rejected"


def test_cancelled_upload_rejects_the_pending_media(password) -> None:
    owner = verified_user("ada@example.com", password)
    client = client_for(owner)
    record = active_property(client, name="Cancelled upload")
    data = jpeg_bytes()
    opened = _open(client, record["id"], kind="photograph", mime="image/jpeg", size=len(data))
    store_upload(opened, data)
    cancelled = expect(
        client.post(
            f"/api/v1/marketplace/media/upload-sessions/{opened['id']}/cancel/",
            format="json",
        ),
        200,
    )
    assert cancelled["upload_status"] == "rejected"
    cancelled_session = MediaUploadSession.objects.get(pk=opened["id"])
    assert cancelled_session.status == "cancelled"
    assert get_object_storage().head(cancelled_session.storage_key) is None
    refused = client.post(_complete(opened["id"]), format="json")
    assert refused.status_code == 409


def test_private_documents_stay_off_public_listings(password) -> None:
    owner = verified_user("ada@example.com", password)
    agent = verified_user("agent@example.com", password)
    stranger = verified_user("stranger@example.com", password)
    staff = staff_user("staff@example.com", password)
    owner_client = client_for(owner)
    agent_client = client_for(agent)
    record = active_property(owner_client, name="Private file")
    mandate = expect(
        owner_client.post(
            MANDATES,
            {
                "property_id": record["id"],
                "listing_scope": "any",
                "representative_user_id": str(agent.id),
                "principal": "self",
                "evidence_reference": "Owner letter 2026",
            },
            format="json",
        ),
        201,
    )
    expect(agent_client.post(f"{MANDATES}{mandate['id']}/accept/"), 200)
    refused = agent_client.post(
        _sessions(record["id"]),
        {"kind": "document", "mime_type": "application/pdf", "size_bytes": 64},
        format="json",
    )
    assert refused.status_code == 403
    photo = add_photo(agent_client, record["id"])
    document = add_document(owner_client, record["id"])
    listing = draft_listing(
        agent_client,
        property_id=record["id"],
        mandate_id=mandate["id"],
        title="Agency photograph",
    )
    attach(agent_client, listing["id"], photo["id"])
    expect(agent_client.post(f"{PUBLISHER}{listing['id']}/submit/"), 200)
    approved = expect(client_for(staff).post(f"{MODERATION}{listing['id']}/approve/"), 200)
    public = expect(APIClient().get(f"{LISTINGS}{approved['reference']}/"), 200)
    blob = str(public)
    assert str(record["id"]) not in blob
    assert "application/pdf" not in blob
    assert document["id"] not in blob
    assert public["media"][0]["asset_reference"].startswith(
        f"/api/v1/marketplace/listings/{approved['reference']}/media/"
    )
    delivered = expect(APIClient().get(public["media"][0]["asset_reference"]), 200)
    assert set(delivered) == {"url", "expires_in"}
    assert str(record["id"]) not in delivered["url"]
    assert "properties/" not in delivered["url"]
    owner_download = expect(
        owner_client.post(
            f"/api/v1/marketplace/properties/{record['id']}/media/{document['id']}/download/"
        ),
        200,
    )
    assert set(owner_download) == {"url", "expires_in"}
    assert str(record["id"]) not in owner_download["url"]
    agent_download = agent_client.post(
        f"/api/v1/marketplace/properties/{record['id']}/media/{document['id']}/download/"
    )
    assert agent_download.status_code == 403
    photo_download = expect(
        agent_client.post(
            f"/api/v1/marketplace/properties/{record['id']}/media/{photo['id']}/download/"
        ),
        200,
    )
    assert "properties/" not in photo_download["url"]
    stranger_download = client_for(stranger).post(
        f"/api/v1/marketplace/properties/{record['id']}/media/{document['id']}/download/"
    )
    assert stranger_download.status_code == 404
    assert ListingEvent.objects.filter(action="media.downloaded").count() == 2


def test_removing_media_deletes_the_object_and_hides_the_listing(password) -> None:
    owner = verified_user("ada@example.com", password)
    staff = staff_user("staff@example.com", password)
    client = client_for(owner)
    record = active_property(client, name="Removable")
    data = jpeg_bytes()
    opened = _open(client, record["id"], kind="photograph", mime="image/jpeg", size=len(data))
    key = store_upload(opened, data)
    MediaUploadSession.objects.filter(pk=opened["id"]).update(
        expires_at=timezone.now() - timedelta(minutes=5)
    )
    replacement = jpeg_bytes()
    _open(client, record["id"], kind="photograph", mime="image/jpeg", size=len(replacement))
    expired = MediaUploadSession.objects.get(pk=opened["id"])
    assert expired.status == "expired"
    assert expired.object_deleted is True
    assert get_object_storage().head(key) is None

    photo = add_photo(client, record["id"])
    mandate = self_mandate(client, owner, record["id"])
    listing = draft_listing(client, property_id=record["id"], mandate_id=mandate["id"])
    attach(client, listing["id"], photo["id"])
    expect(client.post(f"{PUBLISHER}{listing['id']}/submit/"), 200)
    approved = expect(client_for(staff).post(f"{MODERATION}{listing['id']}/approve/"), 200)
    visible = expect(APIClient().get(f"{LISTINGS}{approved['reference']}/"), 200)
    photo_url = visible["media"][0]["asset_reference"]
    stored_key = PropertyMedia.objects.get(pk=photo["id"]).storage_key
    assert get_object_storage().head(stored_key) is not None
    removed = expect(
        client.post(f"/api/v1/marketplace/properties/{record['id']}/media/{photo['id']}/remove/"),
        200,
    )
    assert removed["upload_status"] == "rejected"
    assert get_object_storage().head(stored_key) is None
    assert MediaUploadSession.objects.get(media_id=photo["id"]).object_deleted is True
    hidden = APIClient().get(f"{LISTINGS}{approved['reference']}/")
    assert hidden.status_code == 404
    assert APIClient().get(photo_url).status_code == 404
