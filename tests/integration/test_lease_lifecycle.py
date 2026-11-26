"""Lease draft, approval, reservation expiry, and transition guards."""

from __future__ import annotations

import threading
import uuid
from datetime import date, timedelta

import pytest
from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.utils import timezone
from tests.organizations_support import verified_user
from tests.properties_support import property_body

from apps.identity.domain import AccountStatus
from apps.operations.services.agreements import (
    accept_agreement,
    activate_agreement,
    propose_agreement,
)
from apps.organizations.domain import Permission, permissions_for
from apps.organizations.models import Organization, OrganizationMembership
from apps.properties.domain import AccessOrigin
from apps.properties.models import PropertyAccess
from apps.properties.services.records import create_property
from apps.tenancy.domain import (
    OVERLAPPING_RESERVATION,
    STATUS_INVALID,
    LeasePartyRole,
    LeaseStatus,
    TenancyEventAction,
)
from apps.tenancy.errors import TenancyError
from apps.tenancy.models import Lease, TenancyEvent
from apps.tenancy.services.document import (
    compute_contract_data_hash,
    compute_rendered_document_hash,
    contract_payload_for_lease,
)
from apps.tenancy.services.leases import (
    approve_lease_terms,
    create_draft_lease,
    expire_due_reservations,
)
from apps.tenancy.tasks import expire_due_reservations_task

pytestmark = pytest.mark.django_db

PASSWORD = "Password-123!"
DOCUMENT = b"%PDF-1.7\nGLITTERS lease fixture\n"
START = date(2026, 9, 1)
TERM_END = date(2027, 8, 31)


def _user(prefix: str):
    return verified_user(f"{prefix}-{uuid.uuid4().hex[:8]}@example.com", PASSWORD)


def _organization(name: str = "Prime PM") -> Organization:
    return Organization.objects.create(
        legal_name=f"{name} Ltd",
        display_name=name,
        slug=f"{name.lower().replace(' ', '-')}-{uuid.uuid4().hex[:6]}",
        organization_type="agency",
        status="active",
        country="KE",
    )


def _member(user, organization, role: str = "manager") -> OrganizationMembership:
    return OrganizationMembership.objects.create(
        organization=organization,
        user=user,
        role=role,
        status="active",
        source="creator",
        joined_at=timezone.now(),
    )


def _unit(owner, name: str | None = None):
    label = name or f"Unit {uuid.uuid4().hex[:8]}"
    return create_property(actor=owner, data=property_body(name=label))


def _agreement(owner, agent, prop, organization, *, execute: bool = False):
    proposed = propose_agreement(
        actor=owner,
        property_id=prop.id,
        managing_org_id=organization.id,
        can_execute_leases=execute,
        valid_from=timezone.localdate(),
    )
    accepted = accept_agreement(actor=agent, agreement_id=proposed.id)
    return activate_agreement(actor=owner, agreement_id=accepted.id)


def _payload(tenant, **overrides) -> dict:
    body = {
        "lease_type": "residential_fixed",
        "start_date": START,
        "term_end_date": TERM_END,
        "rent_amount_minor": 4_500_000,
        "rent_currency": "KES",
        "deposit_amount_minor": 4_500_000,
        "parties": [
            {
                "user_id": tenant.id,
                "party_role": LeasePartyRole.PRIMARY_TENANT,
                "has_signature_authority": True,
            }
        ],
        "special_covenants": "Quiet hours after 22:00.",
    }
    body.update(overrides)
    return body


def _draft(actor, prop, tenant, **kwargs):
    payload = kwargs.pop("payload", None) or _payload(tenant)
    return create_draft_lease(
        actor=actor,
        property_id=prop.id,
        payload=payload,
        **kwargs,
    )


def _approve(actor, lease, **kwargs):
    kwargs.setdefault("rendered_document", DOCUMENT)
    return approve_lease_terms(actor=actor, lease_id=lease.id, **kwargs)


def test_authorized_owner_creates_a_draft_without_minting_access() -> None:
    owner = _user("owner")
    tenant = _user("tenant")
    prop = _unit(owner)
    before = set(PropertyAccess.objects.filter(property=prop).values_list("id", flat=True))

    lease = _draft(owner, prop, tenant, idempotency_key="draft-1")

    assert lease.status == LeaseStatus.DRAFT
    assert lease.property_owner_id == owner.id
    assert lease.created_by_id == owner.id
    assert lease.managing_organization_id is None
    assert lease.effective_end_date is None
    assert lease.contract_data_hash == ""
    assert lease.rendered_document_hash == ""
    assert lease.term_end_date == TERM_END
    landlord = lease.parties.get(party_role=LeasePartyRole.LANDLORD)
    primary = lease.parties.get(party_role=LeasePartyRole.PRIMARY_TENANT)
    assert landlord.user_id == owner.id
    assert landlord.has_signature_authority is True
    assert primary.user_id == tenant.id
    assert set(PropertyAccess.objects.filter(property=prop).values_list("id", flat=True)) == before
    assert PropertyAccess.objects.filter(property=prop, origin=AccessOrigin.DIRECT).count() == 1
    actions = set(lease.events.values_list("action", flat=True))
    assert TenancyEventAction.LEASE_DRAFTED in actions
    assert TenancyEventAction.LEASE_PARTY_ADDED in actions

    replay = _draft(owner, prop, tenant, idempotency_key="draft-1")
    assert replay.id == lease.id
    assert Lease.objects.filter(property=prop).count() == 1

    second = _draft(owner, prop, tenant)
    assert second.id != lease.id
    assert second.status == LeaseStatus.DRAFT


def test_unauthorized_and_cross_organization_drafts_are_rejected() -> None:
    owner = _user("owner")
    tenant = _user("tenant")
    member = _user("member")
    outsider = _user("outsider")
    prop = _unit(owner)
    organization = _organization()
    other = _organization("Other PM")
    _member(member, organization, role="member")
    _member(outsider, other, role="manager")
    agent = _user("agent")
    _member(agent, organization, role="manager")
    _agreement(owner, agent, prop, organization)

    with pytest.raises(TenancyError) as anonymous:
        create_draft_lease(
            actor=AnonymousUser(),
            property_id=prop.id,
            payload=_payload(tenant),
        )
    assert anonymous.value.status_code == 401

    owner.status = AccountStatus.SUSPENDED
    owner.save(update_fields=["status", "is_active", "updated_at"])
    with pytest.raises(TenancyError) as suspended:
        _draft(owner, prop, tenant)
    assert suspended.value.status_code == 403
    owner.status = AccountStatus.ACTIVE
    owner.save(update_fields=["status", "is_active", "updated_at"])

    for actor, organization_id in (
        (member, organization.id),
        (outsider, other.id),
        (outsider, organization.id),
        (outsider, None),
    ):
        with pytest.raises(TenancyError) as denied:
            _draft(actor, prop, tenant, organization_id=organization_id)
        assert denied.value.status_code == 403

    building = create_property(
        actor=owner,
        data=property_body(name=f"Block {uuid.uuid4().hex[:6]}", hierarchy_role="building"),
    )
    with pytest.raises(TenancyError) as invalid_unit:
        _draft(owner, building, tenant)
    assert invalid_unit.value.status_code == 400
    assert Lease.objects.filter(property=building).count() == 0


def test_manager_prepares_for_the_owner_and_cannot_approve_without_permission(monkeypatch) -> None:
    owner = _user("owner")
    agent = _user("agent")
    tenant = _user("tenant")
    prop = _unit(owner)
    organization = _organization()
    _member(agent, organization, role="manager")
    agreement = _agreement(owner, agent, prop, organization)
    before = set(PropertyAccess.objects.filter(property=prop).values_list("id", "origin"))

    lease = _draft(agent, prop, tenant, organization_id=organization.id)

    assert lease.property_owner_id == owner.id
    assert lease.created_by_id == agent.id
    assert lease.managing_organization_id == organization.id
    assert lease.management_agreement_id == agreement.id
    assert set(PropertyAccess.objects.filter(property=prop).values_list("id", "origin")) == before
    assert not PropertyAccess.objects.filter(
        property=prop,
        user=agent,
        origin=AccessOrigin.DIRECT,
    ).exists()

    real = permissions_for

    def without_approve(role: str):
        granted = set(real(role))
        granted.discard(Permission.LEASES_APPROVE)
        return frozenset(granted)

    monkeypatch.setattr(
        "apps.organizations.services.authorization.permissions_for",
        without_approve,
    )
    with pytest.raises(TenancyError) as denied:
        _approve(agent, lease)
    assert denied.value.status_code == 403
    lease.refresh_from_db()
    assert lease.status == LeaseStatus.DRAFT
    assert lease.contract_data_hash == ""


def test_approval_stores_hashes_and_reserves_the_exclusive_end() -> None:
    owner = _user("owner")
    tenant = _user("tenant")
    prop = _unit(owner)
    moment = timezone.now()
    lease = _draft(owner, prop, tenant)

    approved = _approve(owner, lease, now=moment, deadline_hours=72, rendered_document_uri="s3://leases/one")

    approved.refresh_from_db()
    assert approved.status == LeaseStatus.PENDING_SIGNATURE
    assert approved.effective_end_date == TERM_END + timedelta(days=1)
    assert approved.signature_deadline == moment + timedelta(hours=72)
    parties = list(approved.parties.all())
    payload = contract_payload_for_lease(approved, parties)
    assert approved.contract_data_hash == compute_contract_data_hash(payload)
    assert approved.rendered_document_hash == compute_rendered_document_hash(DOCUMENT)
    assert "effective_end_date" not in payload["terms"]
    assert approved.events.filter(action=TenancyEventAction.LEASE_TERMS_APPROVED).count() == 1

    with pytest.raises(TenancyError) as again:
        _approve(owner, approved, now=moment)
    assert again.value.status_code == 409
    assert STATUS_INVALID in again.value.detail


def test_incomplete_terms_and_empty_document_do_not_reserve() -> None:
    owner = _user("owner")
    tenant = _user("tenant")
    prop = _unit(owner)
    lease = _draft(owner, prop, tenant)
    primary = lease.parties.get(party_role=LeasePartyRole.PRIMARY_TENANT)
    primary.has_signature_authority = False
    primary.save(update_fields=["has_signature_authority", "updated_at"])

    with pytest.raises(TenancyError) as incomplete:
        _approve(owner, lease)
    assert incomplete.value.status_code == 400
    lease.refresh_from_db()
    assert lease.status == LeaseStatus.DRAFT

    primary.has_signature_authority = True
    primary.save(update_fields=["has_signature_authority", "updated_at"])
    with pytest.raises(TenancyError) as empty:
        _approve(owner, lease, rendered_document=b"")
    assert empty.value.status_code == 400
    lease.refresh_from_db()
    assert lease.status == LeaseStatus.DRAFT
    assert lease.effective_end_date is None


def test_overlapping_reservation_is_rejected_and_gist_still_excludes() -> None:
    owner = _user("owner")
    tenant = _user("tenant")
    other = _user("other")
    prop = _unit(owner)
    Lease.objects.create(
        property=prop,
        property_owner=owner,
        start_date=START,
        term_end_date=TERM_END,
        effective_end_date=TERM_END + timedelta(days=1),
        rent_amount_minor=4_000_000,
        status=LeaseStatus.ACTIVE,
        created_by=owner,
    )
    lease = _draft(owner, prop, tenant)

    with pytest.raises(TenancyError) as conflict:
        _approve(owner, lease)
    assert conflict.value.status_code == 409
    assert conflict.value.detail == OVERLAPPING_RESERVATION
    lease.refresh_from_db()
    assert lease.status == LeaseStatus.DRAFT

    with pytest.raises(
        IntegrityError,
        match="tenancy_lease_exclude_overlapping_contractual_leases",
    ):
        with transaction.atomic():
            Lease.objects.create(
                property=prop,
                property_owner=owner,
                start_date=START + timedelta(days=10),
                term_end_date=TERM_END,
                effective_end_date=TERM_END + timedelta(days=1),
                rent_amount_minor=4_100_000,
                status=LeaseStatus.PENDING_SIGNATURE,
                created_by=other,
            )


def test_pending_terms_and_forbidden_transitions_stay_blocked() -> None:
    owner = _user("owner")
    tenant = _user("tenant")
    prop = _unit(owner)
    lease = _approve(owner, _draft(owner, prop, tenant))

    lease.rent_amount_minor += 1
    with pytest.raises(ValidationError, match="approved"):
        lease.save()
    with pytest.raises(ValidationError, match="approved"):
        Lease.objects.filter(pk=lease.pk).update(rent_amount_minor=1)
    lease.refresh_from_db()
    lease.rent_amount_minor += 1
    with pytest.raises(ValidationError, match="approved"):
        Lease.objects.bulk_update([lease], ["rent_amount_minor"])

    lease.refresh_from_db()
    lease.status = LeaseStatus.DRAFT
    with pytest.raises(ValidationError, match="not allowed"):
        lease.save()
    with pytest.raises(ValidationError, match="not allowed"):
        Lease.objects.filter(pk=lease.pk).update(status=LeaseStatus.DRAFT)
    lease.refresh_from_db()
    assert lease.status == LeaseStatus.PENDING_SIGNATURE
    assert lease.rent_amount_minor == 4_500_000


def test_reservation_expiry_boundary_is_idempotent() -> None:
    owner = _user("owner")
    tenant = _user("tenant")
    later = _user("later")
    prop = _unit(owner)
    moment = timezone.now()
    due = _approve(owner, _draft(owner, prop, tenant), now=moment, deadline_hours=1)
    held = _draft(
        owner,
        prop,
        later,
        payload=_payload(
            later,
            start_date=date(2028, 1, 1),
            term_end_date=date(2028, 12, 31),
        ),
    )
    held = _approve(owner, held, now=moment, deadline_hours=48)

    assert expire_due_reservations(
        property_id=prop.id,
        actor=owner,
        now=due.signature_deadline - timedelta(microseconds=1),
    ) == 0
    due.refresh_from_db()
    assert due.status == LeaseStatus.PENDING_SIGNATURE

    assert expire_due_reservations(
        property_id=prop.id,
        actor=owner,
        now=due.signature_deadline,
    ) == 1
    due.refresh_from_db()
    held.refresh_from_db()
    assert due.status == LeaseStatus.EXPIRED_UNSIGNED
    assert held.status == LeaseStatus.PENDING_SIGNATURE
    expiry_events = TenancyEvent.objects.filter(
        lease=due,
        action=TenancyEventAction.LEASE_RESERVATION_EXPIRED,
    )
    assert expiry_events.count() == 1

    repeated = expire_due_reservations(
        property_id=prop.id,
        actor=owner,
        now=due.signature_deadline,
    )
    assert repeated == 0
    assert expiry_events.count() == 1

    with pytest.raises(TenancyError) as sweep:
        expire_due_reservations(actor=owner, now=held.signature_deadline)
    assert sweep.value.status_code == 403

    assert expire_due_reservations_task() == 0
    assert expire_due_reservations(trusted_system=True, now=held.signature_deadline) == 1
    assert expire_due_reservations(trusted_system=True, now=held.signature_deadline) == 0
    assert (
        settings.CELERY_BEAT_SCHEDULE["tenancy-expire-due-reservations"]["task"]
        == "tenancy.expire_due_reservations"
    )
    assert settings.CELERY_BEAT_SCHEDULE["tenancy-expire-due-reservations"]["schedule"] == 900


@pytest.mark.django_db(transaction=True)
def test_concurrent_approvals_of_one_draft_reserve_once() -> None:
    owner = _user("owner")
    tenant = _user("tenant")
    prop = _unit(owner)
    lease = _draft(owner, prop, tenant)
    results: list[int] = []

    def attempt() -> None:
        try:
            _approve(owner, lease)
            results.append(200)
        except TenancyError as exc:
            results.append(exc.status_code)
        finally:
            connection.close()

    threads = [threading.Thread(target=attempt), threading.Thread(target=attempt)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(results) == [200, 409]
    lease.refresh_from_db()
    assert lease.status == LeaseStatus.PENDING_SIGNATURE
    assert lease.events.filter(action=TenancyEventAction.LEASE_TERMS_APPROVED).count() == 1


@pytest.mark.django_db(transaction=True)
def test_concurrent_overlapping_approvals_keep_one_reservation() -> None:
    owner = _user("owner")
    first = _user("first")
    second = _user("second")
    prop = _unit(owner)
    left = _draft(owner, prop, first)
    right = _draft(owner, prop, second)
    results: list[int] = []

    def attempt(lease) -> None:
        try:
            _approve(owner, lease)
            results.append(200)
        except TenancyError as exc:
            results.append(exc.status_code)
        finally:
            connection.close()

    threads = [
        threading.Thread(target=attempt, args=(left,)),
        threading.Thread(target=attempt, args=(right,)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(results) == [200, 409]
    statuses = set(
        Lease.objects.filter(pk__in=[left.id, right.id]).values_list("status", flat=True)
    )
    assert statuses == {LeaseStatus.PENDING_SIGNATURE, LeaseStatus.DRAFT}


@pytest.mark.django_db(transaction=True)
def test_concurrent_expiry_writes_one_event() -> None:
    owner = _user("owner")
    tenant = _user("tenant")
    prop = _unit(owner)
    moment = timezone.now()
    lease = _approve(owner, _draft(owner, prop, tenant), now=moment, deadline_hours=1)
    counts: list[int] = []

    def attempt() -> None:
        try:
            counts.append(
                expire_due_reservations(
                    property_id=prop.id,
                    actor=owner,
                    now=lease.signature_deadline,
                )
            )
        finally:
            connection.close()

    threads = [threading.Thread(target=attempt), threading.Thread(target=attempt)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sum(counts) == 1
    lease.refresh_from_db()
    assert lease.status == LeaseStatus.EXPIRED_UNSIGNED
    assert (
        TenancyEvent.objects.filter(
            lease=lease,
            action=TenancyEventAction.LEASE_RESERVATION_EXPIRED,
        ).count()
        == 1
    )
