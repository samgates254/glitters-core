"""Enquiry rows, privacy constraints, and append-only events.

These tests use the database. They do not mock persistence.
"""

from __future__ import annotations

import uuid

import pytest
from django.db import IntegrityError, connection, transaction
from django.db.models import ProtectedError
from django.utils import timezone
from tests.marketplace_support import active_property, draft_listing, self_mandate
from tests.organizations_support import client_for, verified_user

from apps.marketplace.domain import (
    EnquiryEventAction,
    EnquiryStatus,
    enquiry_event_metadata,
    enquiry_transition_allowed,
)
from apps.marketplace.models import Enquiry, EnquiryEvent, Listing
from apps.organizations.domain import OrganizationType
from apps.organizations.models import Organization

ALLOWED_TRANSITIONS = (
    (EnquiryStatus.NEW, EnquiryStatus.CONTACTED),
    (EnquiryStatus.NEW, EnquiryStatus.SPAM),
    (EnquiryStatus.NEW, EnquiryStatus.CLOSED),
    (EnquiryStatus.CONTACTED, EnquiryStatus.QUALIFIED),
    (EnquiryStatus.CONTACTED, EnquiryStatus.UNQUALIFIED),
    (EnquiryStatus.CONTACTED, EnquiryStatus.SPAM),
    (EnquiryStatus.CONTACTED, EnquiryStatus.CLOSED),
    (EnquiryStatus.QUALIFIED, EnquiryStatus.CONVERTED),
    (EnquiryStatus.QUALIFIED, EnquiryStatus.SPAM),
    (EnquiryStatus.QUALIFIED, EnquiryStatus.CLOSED),
    (EnquiryStatus.UNQUALIFIED, EnquiryStatus.CLOSED),
)

FORBIDDEN_TRANSITIONS = (
    (EnquiryStatus.NEW, EnquiryStatus.QUALIFIED),
    (EnquiryStatus.NEW, EnquiryStatus.UNQUALIFIED),
    (EnquiryStatus.NEW, EnquiryStatus.CONVERTED),
    (EnquiryStatus.NEW, EnquiryStatus.NEW),
    (EnquiryStatus.CONTACTED, EnquiryStatus.NEW),
    (EnquiryStatus.CONTACTED, EnquiryStatus.CONVERTED),
    (EnquiryStatus.QUALIFIED, EnquiryStatus.UNQUALIFIED),
    (EnquiryStatus.QUALIFIED, EnquiryStatus.CONTACTED),
    (EnquiryStatus.UNQUALIFIED, EnquiryStatus.SPAM),
    (EnquiryStatus.UNQUALIFIED, EnquiryStatus.QUALIFIED),
    (EnquiryStatus.CONVERTED, EnquiryStatus.CLOSED),
    (EnquiryStatus.CONVERTED, EnquiryStatus.SPAM),
    (EnquiryStatus.SPAM, EnquiryStatus.CLOSED),
    (EnquiryStatus.SPAM, EnquiryStatus.NEW),
    (EnquiryStatus.CLOSED, EnquiryStatus.NEW),
    (EnquiryStatus.CLOSED, EnquiryStatus.CONTACTED),
    ("missing", EnquiryStatus.CLOSED),
)


def _reject_privacy(enquiry: Enquiry, **fields) -> None:
    with (
        pytest.raises(IntegrityError, match="marketplace_enquiry_privacy_state"),
        transaction.atomic(),
    ):
        Enquiry.objects.filter(pk=enquiry.pk).update(**fields)


@pytest.fixture
def enquiry_context(password: str):
    suffix = uuid.uuid4().hex[:8]
    owner = verified_user(f"enquiry-owner-{suffix}@example.com", password)
    client = client_for(owner)
    record = active_property(client, name=f"Enquiry {suffix}")
    mandate = self_mandate(client, owner, record["id"])
    created = draft_listing(
        client,
        property_id=record["id"],
        mandate_id=mandate["id"],
        title="Enquiry listing",
    )
    return owner, Listing.objects.get(pk=created["id"])


def _enquiry(listing: Listing, advertiser, **overrides) -> Enquiry:
    payload = {
        "listing": listing,
        "advertiser": advertiser,
        "contact_name": "Ada Lovelace",
        "contact_email": "seeker@example.com",
        "contact_phone": "+254712345678",
        "message": "Is the apartment still available?",
        "consent_at": timezone.now(),
        "consent_version": "2026-11",
    }
    payload.update(overrides)
    return Enquiry.objects.create(**payload)


def _anonymize(enquiry: Enquiry) -> Enquiry:
    when = timezone.now()
    Enquiry.objects.filter(pk=enquiry.pk).update(
        contact_name=None,
        contact_email=None,
        contact_phone=None,
        message=None,
        anonymized_at=when,
    )
    enquiry.refresh_from_db()
    return enquiry


@pytest.mark.parametrize(("current", "new"), ALLOWED_TRANSITIONS)
def test_allowed_enquiry_transitions(current: str, new: str) -> None:
    assert enquiry_transition_allowed(current, new)


@pytest.mark.parametrize(("current", "new"), FORBIDDEN_TRANSITIONS)
def test_forbidden_enquiry_transitions(current: str, new: str) -> None:
    assert not enquiry_transition_allowed(current, new)


def test_terminal_statuses_have_no_exit() -> None:
    assert EnquiryStatus.TERMINAL == frozenset(
        {EnquiryStatus.CONVERTED, EnquiryStatus.SPAM, EnquiryStatus.CLOSED}
    )
    for status in EnquiryStatus.TERMINAL:
        assert not enquiry_transition_allowed(status, EnquiryStatus.CLOSED)
        assert not enquiry_transition_allowed(status, EnquiryStatus.NEW)


@pytest.mark.django_db
def test_enquiry_constraints_and_indexes_exist() -> None:
    with connection.cursor() as cursor:
        enquiry = connection.introspection.get_constraints(cursor, "marketplace_enquiry")
        event = connection.introspection.get_constraints(cursor, "marketplace_enquiry_event")
    assert {
        "marketplace_enquiry_status_valid",
        "marketplace_enquiry_privacy_state",
        "marketplace_enquiry_closed_timestamp",
        "mkt_enquiry_listing_created",
        "mkt_enquiry_advertiser",
        "mkt_enquiry_org_status",
        "mkt_enquiry_assignee",
    } <= set(enquiry)
    assert {"mkt_enquiry_event_action_valid", "mkt_enquiry_event_created"} <= set(event)


@pytest.mark.django_db
def test_valid_enquiry_and_enquiry_without_a_phone(enquiry_context) -> None:
    owner, listing = enquiry_context
    stored = _enquiry(listing, owner)
    assert isinstance(stored.id, uuid.UUID)
    assert stored.status == EnquiryStatus.NEW
    assert stored.organization_id is None
    assert stored.assigned_to_id is None
    assert stored.closed_at is None
    assert stored.anonymized_at is None
    assert stored.created_at is not None
    assert stored.updated_at is not None
    assert stored.contact_phone == "+254712345678"

    without_phone = _enquiry(
        listing,
        owner,
        contact_email="other@example.com",
        contact_phone=None,
    )
    without_phone.refresh_from_db()
    assert without_phone.contact_phone is None
    assert without_phone.message == "Is the apartment still available?"


@pytest.mark.django_db
def test_active_enquiry_rejects_missing_or_blank_contact_fields(enquiry_context) -> None:
    owner, listing = enquiry_context
    enquiry = _enquiry(listing, owner)
    _reject_privacy(enquiry, contact_name=None)
    _reject_privacy(enquiry, contact_name="")
    _reject_privacy(enquiry, contact_email=None)
    _reject_privacy(enquiry, contact_email="")
    _reject_privacy(enquiry, message=None)
    _reject_privacy(enquiry, message="")
    _reject_privacy(enquiry, contact_phone="")
    enquiry.refresh_from_db()
    assert enquiry.contact_name == "Ada Lovelace"
    assert enquiry.contact_email == "seeker@example.com"
    assert enquiry.message == "Is the apartment still available?"


@pytest.mark.django_db
def test_partial_anonymization_is_rejected(enquiry_context) -> None:
    owner, listing = enquiry_context
    enquiry = _enquiry(listing, owner)
    when = timezone.now()
    cleared = {
        "anonymized_at": when,
        "contact_name": None,
        "contact_email": None,
        "contact_phone": None,
        "message": None,
    }
    _reject_privacy(enquiry, **cleared | {"contact_name": "Ada Lovelace"})
    _reject_privacy(enquiry, **cleared | {"contact_email": "seeker@example.com"})
    _reject_privacy(enquiry, **cleared | {"contact_phone": "+254712345678"})
    _reject_privacy(enquiry, **cleared | {"contact_phone": ""})
    _reject_privacy(enquiry, **cleared | {"message": "Still here"})
    _reject_privacy(
        enquiry,
        anonymized_at=None,
        contact_name=None,
        contact_email=None,
        contact_phone=None,
        message=None,
    )
    enquiry.refresh_from_db()
    assert enquiry.anonymized_at is None
    assert enquiry.contact_name == "Ada Lovelace"


@pytest.mark.django_db
def test_complete_anonymization_and_no_return_of_contact_data(enquiry_context) -> None:
    owner, listing = enquiry_context
    enquiry = _anonymize(_enquiry(listing, owner))
    assert enquiry.anonymized_at is not None
    assert enquiry.contact_name is None
    assert enquiry.contact_email is None
    assert enquiry.contact_phone is None
    assert enquiry.message is None

    _reject_privacy(enquiry, contact_name="Ada Lovelace")
    _reject_privacy(enquiry, contact_email="seeker@example.com")
    _reject_privacy(enquiry, contact_phone="+254712345678")
    _reject_privacy(enquiry, message="Please call me")
    _reject_privacy(
        enquiry,
        contact_name="Ada Lovelace",
        contact_email="seeker@example.com",
        contact_phone="+254712345678",
        message="Please call me",
    )
    enquiry.refresh_from_db()
    assert enquiry.contact_name is None
    assert enquiry.contact_email is None
    assert enquiry.contact_phone is None
    assert enquiry.message is None
    assert enquiry.anonymized_at is not None


@pytest.mark.django_db
def test_status_closure_and_assignment_are_separate(enquiry_context, password: str) -> None:
    owner, listing = enquiry_context
    assignee = verified_user("enquiry-assignee@example.com", password)
    later = verified_user("enquiry-later@example.com", password)
    enquiry = _enquiry(listing, owner, assigned_to=assignee)
    assert enquiry.status == EnquiryStatus.NEW
    assert enquiry.assigned_to_id == assignee.id

    # The database stores a jump the status graph forbids. The service must refuse it.
    Enquiry.objects.filter(pk=enquiry.pk).update(status=EnquiryStatus.QUALIFIED)
    enquiry.refresh_from_db()
    assert enquiry.status == EnquiryStatus.QUALIFIED
    assert enquiry.assigned_to_id == assignee.id
    assert not enquiry_transition_allowed(EnquiryStatus.NEW, EnquiryStatus.QUALIFIED)

    with (
        pytest.raises(IntegrityError, match="marketplace_enquiry_status_valid"),
        transaction.atomic(),
    ):
        Enquiry.objects.filter(pk=enquiry.pk).update(status="archived")

    with (
        pytest.raises(IntegrityError, match="marketplace_enquiry_closed_timestamp"),
        transaction.atomic(),
    ):
        Enquiry.objects.filter(pk=enquiry.pk).update(closed_at=timezone.now())
    with (
        pytest.raises(IntegrityError, match="marketplace_enquiry_closed_timestamp"),
        transaction.atomic(),
    ):
        Enquiry.objects.filter(pk=enquiry.pk).update(
            status=EnquiryStatus.CLOSED,
            closed_at=None,
        )
    with (
        pytest.raises(IntegrityError, match="marketplace_enquiry_closed_timestamp"),
        transaction.atomic(),
    ):
        Enquiry.objects.filter(pk=enquiry.pk).update(
            status=EnquiryStatus.CONVERTED,
            closed_at=timezone.now(),
        )

    closed_at = timezone.now()
    Enquiry.objects.filter(pk=enquiry.pk).update(
        status=EnquiryStatus.CLOSED,
        closed_at=closed_at,
        assigned_to=later,
    )
    enquiry.refresh_from_db()
    assert enquiry.status == EnquiryStatus.CLOSED
    assert enquiry.closed_at is not None
    assert enquiry.assigned_to_id == later.id

    converted = _enquiry(
        listing,
        owner,
        contact_email="converted@example.com",
        status=EnquiryStatus.CONVERTED,
        assigned_to=assignee,
    )
    assert converted.closed_at is None
    assert converted.assigned_to_id == assignee.id
    Enquiry.objects.filter(pk=converted.pk).update(assigned_to=None)
    converted.refresh_from_db()
    assert converted.status == EnquiryStatus.CONVERTED
    assert converted.assigned_to_id is None


@pytest.mark.django_db
def test_enquiry_event_is_append_only_and_metadata_is_allowlisted(
    enquiry_context, password: str
) -> None:
    owner, listing = enquiry_context
    enquiry = _enquiry(listing, owner)
    actor = verified_user("enquiry-actor@example.com", password)
    event = EnquiryEvent.objects.create(
        enquiry=enquiry,
        actor=actor,
        action=EnquiryEventAction.CREATED,
        request_id="req-enquiry-1",
        metadata={"consent_version": "2026-11", "channel": "web"},
    )
    assert isinstance(event.id, uuid.UUID)
    assert event.enquiry_id == enquiry.id
    assert event.metadata == {"consent_version": "2026-11", "channel": "web"}
    assert event.created_at is not None

    with pytest.raises(ValueError, match="not allowed") as rejected:
        EnquiryEvent.objects.create(
            enquiry=enquiry,
            action=EnquiryEventAction.VIEWED,
            metadata={"contact_email": "seeker@example.com", "message": "call me"},
        )
    assert "seeker@example.com" not in str(rejected.value)
    assert EnquiryEvent.objects.filter(enquiry=enquiry).count() == 1

    with pytest.raises(ValueError, match="not allowed"):
        enquiry_event_metadata({"channel": "ada@example.com"})
    with pytest.raises(ValueError, match="must be an object"):
        enquiry_event_metadata(["contact_name"])

    with pytest.raises(ValueError, match="not allowed"):
        EnquiryEvent.objects.bulk_create(
            [
                EnquiryEvent(enquiry=enquiry, action=EnquiryEventAction.VIEWED, metadata={}),
                EnquiryEvent(
                    enquiry=enquiry,
                    action=EnquiryEventAction.VIEWED,
                    metadata={"message": "private note"},
                ),
            ]
        )
    assert EnquiryEvent.objects.filter(enquiry=enquiry).count() == 1

    stored = EnquiryEvent.objects.bulk_create(
        [
            EnquiryEvent(
                enquiry=enquiry,
                action=EnquiryEventAction.STATUS_CHANGED,
                metadata={"from_status": EnquiryStatus.NEW, "to_status": EnquiryStatus.CONTACTED},
            )
        ]
    )
    assert stored[0].metadata == {
        "from_status": EnquiryStatus.NEW,
        "to_status": EnquiryStatus.CONTACTED,
    }

    with pytest.raises(ValueError, match="append-only"):
        event.save()
    with pytest.raises(ValueError, match="append-only"):
        EnquiryEvent.objects.filter(pk=event.pk).update(action=EnquiryEventAction.VIEWED)
    with pytest.raises(ValueError, match="append-only"):
        EnquiryEvent.objects.bulk_update([event], ["action"])
    with pytest.raises(ValueError, match="append-only"):
        event.delete()
    with pytest.raises(ValueError, match="append-only"):
        EnquiryEvent.objects.filter(pk=event.pk).delete()
    event.refresh_from_db()
    assert event.action == EnquiryEventAction.CREATED
    assert event.actor_id == actor.id

    with (
        pytest.raises(IntegrityError, match="mkt_enquiry_event_action_valid"),
        transaction.atomic(),
    ):
        EnquiryEvent.objects.create(enquiry=enquiry, action="enquiry.deleted")


@pytest.mark.django_db
def test_enquiry_relationships_protect_history(enquiry_context, password: str) -> None:
    owner, listing = enquiry_context
    organization = Organization.objects.create(
        legal_name="North Agency Limited",
        display_name="North Agency Enquiry",
        slug=f"north-enquiry-{uuid.uuid4().hex[:8]}",
        organization_type=OrganizationType.AGENCY,
    )
    assignee = verified_user(f"assignee-{uuid.uuid4().hex[:8]}@example.com", password)
    actor = verified_user(f"actor-{uuid.uuid4().hex[:8]}@example.com", password)
    enquiry = _enquiry(listing, owner, organization=organization, assigned_to=assignee)
    event = EnquiryEvent.objects.create(
        enquiry=enquiry,
        actor=actor,
        action=EnquiryEventAction.ASSIGNED,
        metadata={"assignee_id": str(assignee.id)},
    )

    with pytest.raises(ProtectedError) as listing_block, transaction.atomic():
        listing.delete()
    assert enquiry.id in {row.pk for row in listing_block.value.protected_objects}

    with pytest.raises(ProtectedError) as organization_block, transaction.atomic():
        organization.delete()
    assert enquiry.id in {row.pk for row in organization_block.value.protected_objects}

    with pytest.raises(ProtectedError) as enquiry_block, transaction.atomic():
        enquiry.delete()
    assert event.id in {row.pk for row in enquiry_block.value.protected_objects}
    assert Enquiry.objects.filter(pk=enquiry.pk).exists()
    assert EnquiryEvent.objects.filter(pk=event.pk, enquiry_id=enquiry.id).exists()

    assignee.delete()
    actor.delete()
    enquiry.refresh_from_db()
    event.refresh_from_db()
    assert enquiry.assigned_to_id is None
    assert enquiry.advertiser_id == owner.id
    assert event.actor_id is None
    assert event.action == EnquiryEventAction.ASSIGNED

    with pytest.raises(ProtectedError) as advertiser_block, transaction.atomic():
        owner.delete()
    assert enquiry.id in {row.pk for row in advertiser_block.value.protected_objects}
