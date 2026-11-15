"""Integration tests for enquiry assignment and lifecycle transitions (M6-B.4)."""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest
from django.db import IntegrityError
from django.utils import timezone
from tests.identity_support import error_detail
from tests.marketplace_support import (
    MANDATES,
    active_property,
    draft_listing,
    expect,
    self_mandate,
    staff_user,
)
from tests.organizations_support import ORGANIZATIONS, client_for, organization_body, verified_user

from apps.identity.domain import AccountStatus
from apps.marketplace.domain import (
    ASSIGNEE_NOT_ELIGIBLE,
    ENQUIRY_TRANSITION_INVALID,
    MEMBERSHIP_NOT_ACTIVE,
    NOT_FOUND,
    ORG_NOT_ACTIVE,
    PERMISSION_DENIED,
    EnquiryEventAction,
    EnquiryStatus,
)
from apps.marketplace.models import Enquiry, EnquiryEvent, Listing
from apps.organizations.domain import MembershipSource, MembershipStatus, OrganizationStatus
from apps.organizations.models import Organization, OrganizationMembership

pytestmark = pytest.mark.django_db

ENQUIRIES = "/api/v1/marketplace/enquiries/"


def _store(listing: Listing, **overrides) -> Enquiry:
    payload = {
        "listing": listing,
        "advertiser_id": listing.advertiser_id,
        "organization_id": listing.advertising_organization_id,
        "contact_name": "Ada Lovelace",
        "contact_email": "seeker@example.com",
        "contact_phone": "+254712345678",
        "message": "Is the apartment still available?",
        "consent_at": timezone.now(),
        "consent_version": "2026-11",
    }
    payload.update(overrides)
    return Enquiry.objects.create(**payload)


def _personal(password: str, suffix: str):
    owner = verified_user(f"ada-{suffix}@example.com", password)
    client = client_for(owner)
    record = active_property(client, name=f"Home {suffix}")
    mandate = self_mandate(client, owner, record["id"])
    created = draft_listing(
        client,
        property_id=record["id"],
        mandate_id=mandate["id"],
        title=f"Title {suffix}",
    )
    return owner, client, Listing.objects.get(pk=created["id"])


def _join(organization_id, user, role: str) -> OrganizationMembership:
    return OrganizationMembership.objects.create(
        organization_id=organization_id,
        user=user,
        role=role,
        status=MembershipStatus.ACTIVE,
        source=MembershipSource.INVITATION,
        joined_at=timezone.now(),
    )


def _organization_enquiry(password: str, suffix: str, roles: tuple[str, ...] = ()):
    owner = verified_user(f"owner-{suffix}@example.com", password)
    owner_client = client_for(owner)
    agency = expect(
        owner_client.post(
            ORGANIZATIONS,
            organization_body(
                display_name=f"Agency {suffix}",
                legal_name=f"Agency {suffix} Limited",
            ),
            format="json",
        ),
        201,
    )
    people = {"owner": owner}
    for role in roles:
        person = verified_user(f"{role}-{suffix}@example.com", password)
        _join(agency["id"], person, role)
        people[role] = person
    record = active_property(owner_client, name=f"Agency home {suffix}")
    mandate = expect(
        owner_client.post(
            MANDATES,
            {
                "property_id": record["id"],
                "listing_scope": "sale",
                "representative_organization_id": agency["id"],
                "principal": "self",
            },
            format="json",
        ),
        201,
    )
    if mandate["status"] != "active":
        mandate = expect(owner_client.post(f"{MANDATES}{mandate['id']}/accept/"), 200)
    created = draft_listing(
        owner_client,
        property_id=record["id"],
        mandate_id=mandate["id"],
        title=f"Agency title {suffix}",
    )
    listing = Listing.objects.get(pk=created["id"])
    enquiry = _store(
        listing,
        contact_email=f"seeker-{suffix}@example.com",
        message=f"Message {suffix}",
    )
    return people, agency, listing, enquiry


def test_personal_enquiry_assignment_and_lifecycle(password: str) -> None:
    suffix = uuid.uuid4().hex[:8]
    owner, client, listing = _personal(password, suffix)
    enquiry = _store(listing)

    # 1. Personal advertiser can assign to self
    assign_url = f"{ENQUIRIES}{enquiry.id}/assignment/"
    resp = expect(client.patch(assign_url, {"user_id": str(owner.id)}, format="json"), 200)
    assert resp["assignment"] == {"user_id": str(owner.id)}
    assert resp["status"] == EnquiryStatus.NEW  # status unchanged
    enquiry.refresh_from_db()
    assert enquiry.assigned_to_id == owner.id

    event = EnquiryEvent.objects.filter(enquiry=enquiry, action=EnquiryEventAction.ASSIGNED).first()
    assert event is not None
    assert event.metadata == {"assignee_id": str(owner.id)}
    assert event.actor_id == owner.id

    # 2. Personal advertiser cannot assign to another user
    other = verified_user(f"other-{suffix}@example.com", password)
    bad_assign = client.patch(assign_url, {"user_id": str(other.id)}, format="json")
    assert bad_assign.status_code == 400
    assert error_detail(bad_assign) == ASSIGNEE_NOT_ELIGIBLE

    # 3. Personal advertiser can unassign
    resp = expect(client.patch(assign_url, {"user_id": None}, format="json"), 200)
    assert resp["assignment"] is None
    enquiry.refresh_from_db()
    assert enquiry.assigned_to_id is None

    unassign_event = EnquiryEvent.objects.filter(
        enquiry=enquiry, action=EnquiryEventAction.UNASSIGNED
    ).first()
    assert unassign_event is not None
    assert unassign_event.metadata == {"previous_assignee_id": str(owner.id)}

    # 4. Stranger cannot assign or transition personal enquiry -> 404
    stranger = verified_user(f"stranger-{suffix}@example.com", password)
    stranger_client = client_for(stranger)
    assert (
        stranger_client.patch(assign_url, {"user_id": str(owner.id)}, format="json").status_code
        == 404
    )
    transition_url = f"{ENQUIRIES}{enquiry.id}/transitions/"
    assert (
        stranger_client.post(
            transition_url, {"status": EnquiryStatus.CONTACTED}, format="json"
        ).status_code
        == 404
    )

    # 5. Personal advertiser can transition status through lifecycle
    trans_resp = expect(
        client.post(transition_url, {"status": EnquiryStatus.CONTACTED}, format="json"), 200
    )
    assert trans_resp["status"] == EnquiryStatus.CONTACTED
    enquiry.refresh_from_db()
    assert enquiry.status == EnquiryStatus.CONTACTED

    t_event = EnquiryEvent.objects.filter(
        enquiry=enquiry, action=EnquiryEventAction.STATUS_CHANGED
    ).first()
    assert t_event is not None
    assert t_event.metadata == {
        "from_status": EnquiryStatus.NEW,
        "to_status": EnquiryStatus.CONTACTED,
    }


def test_organization_roles_and_permissions(password: str) -> None:
    suffix = uuid.uuid4().hex[:8]
    roles = ("admin", "manager", "member", "viewer")
    people, agency, listing, enquiry = _organization_enquiry(password, suffix, roles)

    assign_url = f"{ENQUIRIES}{enquiry.id}/assignment/"
    transition_url = f"{ENQUIRIES}{enquiry.id}/transitions/"

    # Owner, Admin, Manager have leads.manage -> can assign and transition
    for privileged_role in ("owner", "admin", "manager"):
        client = client_for(people[privileged_role])
        assignee = people["member"]
        resp = expect(client.patch(assign_url, {"user_id": str(assignee.id)}, format="json"), 200)
        assert resp["assignment"] == {"user_id": str(assignee.id)}

        # Clear assignment for next iteration
        expect(client.patch(assign_url, {"user_id": None}, format="json"), 200)

    # Member and Viewer lack leads.manage -> 403 PERMISSION_DENIED
    for unprivileged_role in ("member", "viewer"):
        client = client_for(people[unprivileged_role])
        assign_resp = client.patch(
            assign_url, {"user_id": str(people["manager"].id)}, format="json"
        )
        assert assign_resp.status_code == 403
        assert error_detail(assign_resp) == PERMISSION_DENIED

        trans_resp = client.post(transition_url, {"status": EnquiryStatus.CONTACTED}, format="json")
        assert trans_resp.status_code == 403
        assert error_detail(trans_resp) == PERMISSION_DENIED

    # Inactive membership of actor -> 403 MEMBERSHIP_NOT_ACTIVE
    manager_membership = OrganizationMembership.objects.get(
        organization_id=agency["id"], user=people["manager"]
    )
    manager_membership.status = MembershipStatus.SUSPENDED
    manager_membership.save(update_fields=["status"])
    manager_client = client_for(people["manager"])

    assign_inactive = manager_client.patch(
        assign_url, {"user_id": str(people["member"].id)}, format="json"
    )
    assert assign_inactive.status_code == 403
    assert error_detail(assign_inactive) == MEMBERSHIP_NOT_ACTIVE

    trans_inactive = manager_client.post(
        transition_url, {"status": EnquiryStatus.CONTACTED}, format="json"
    )
    assert trans_inactive.status_code == 403
    assert error_detail(trans_inactive) == MEMBERSHIP_NOT_ACTIVE

    # Stranger (outside organization) -> 404 NOT_FOUND
    stranger = verified_user(f"org-stranger-{suffix}@example.com", password)
    stranger_client = client_for(stranger)
    stranger_assign = stranger_client.patch(
        assign_url, {"user_id": str(people["manager"].id)}, format="json"
    )
    assert stranger_assign.status_code == 404
    assert error_detail(stranger_assign) == NOT_FOUND

    stranger_trans = stranger_client.post(
        transition_url, {"status": EnquiryStatus.CONTACTED}, format="json"
    )
    assert stranger_trans.status_code == 404
    assert error_detail(stranger_trans) == NOT_FOUND


def test_cross_organization_and_inactive_assignee_rejected(password: str) -> None:
    suffix = uuid.uuid4().hex[:8]
    people, agency, listing, enquiry = _organization_enquiry(
        password, suffix, ("manager", "member")
    )
    owner_client = client_for(people["owner"])
    assign_url = f"{ENQUIRIES}{enquiry.id}/assignment/"

    # 1. Cross-organization assignee (active member of another org)
    other_agency_owner = verified_user(f"other-owner-{suffix}@example.com", password)
    other_client = client_for(other_agency_owner)
    other_agency = expect(
        other_client.post(
            ORGANIZATIONS,
            organization_body(
                display_name=f"Other Agency {suffix}",
                legal_name=f"Other Agency {suffix} Limited",
            ),
            format="json",
        ),
        201,
    )
    cross_org_user = verified_user(f"cross-user-{suffix}@example.com", password)
    _join(other_agency["id"], cross_org_user, "member")

    resp = owner_client.patch(assign_url, {"user_id": str(cross_org_user.id)}, format="json")
    assert resp.status_code == 400
    assert error_detail(resp) == ASSIGNEE_NOT_ELIGIBLE

    # 2. Inactive membership in same org
    suspended_member = verified_user(f"suspended-mem-{suffix}@example.com", password)
    membership = _join(agency["id"], suspended_member, "member")
    membership.status = MembershipStatus.SUSPENDED
    membership.save(update_fields=["status"])

    resp = owner_client.patch(assign_url, {"user_id": str(suspended_member.id)}, format="json")
    assert resp.status_code == 400
    assert error_detail(resp) == ASSIGNEE_NOT_ELIGIBLE

    # 3. Suspended user account in same org
    suspended_user = verified_user(f"suspended-user-{suffix}@example.com", password)
    _join(agency["id"], suspended_user, "member")
    suspended_user.status = AccountStatus.SUSPENDED
    suspended_user.save(update_fields=["status", "updated_at"])

    resp = owner_client.patch(assign_url, {"user_id": str(suspended_user.id)}, format="json")
    assert resp.status_code == 400
    assert error_detail(resp) == ASSIGNEE_NOT_ELIGIBLE


def test_inactive_organization_rejected(password: str) -> None:
    suffix = uuid.uuid4().hex[:8]
    people, agency, listing, enquiry = _organization_enquiry(password, suffix, ("manager",))
    owner_client = client_for(people["owner"])
    assign_url = f"{ENQUIRIES}{enquiry.id}/assignment/"
    transition_url = f"{ENQUIRIES}{enquiry.id}/transitions/"

    # Suspend the organization
    Organization.objects.filter(pk=agency["id"]).update(status=OrganizationStatus.SUSPENDED)

    resp_assign = owner_client.patch(
        assign_url, {"user_id": str(people["manager"].id)}, format="json"
    )
    assert resp_assign.status_code == 403
    assert error_detail(resp_assign) == ORG_NOT_ACTIVE

    resp_trans = owner_client.post(
        transition_url, {"status": EnquiryStatus.CONTACTED}, format="json"
    )
    assert resp_trans.status_code == 403
    assert error_detail(resp_trans) == ORG_NOT_ACTIVE


def test_reassignment_and_unassignment_events(password: str) -> None:
    suffix = uuid.uuid4().hex[:8]
    people, agency, listing, enquiry = _organization_enquiry(
        password, suffix, ("manager", "member")
    )
    owner_client = client_for(people["owner"])
    assign_url = f"{ENQUIRIES}{enquiry.id}/assignment/"

    manager = people["manager"]
    member = people["member"]

    # Assign to Manager
    expect(owner_client.patch(assign_url, {"user_id": str(manager.id)}, format="json"), 200)
    enquiry.refresh_from_db()
    assert enquiry.assigned_to_id == manager.id

    e1 = EnquiryEvent.objects.filter(enquiry=enquiry, action=EnquiryEventAction.ASSIGNED).latest(
        "created_at"
    )
    assert e1.metadata == {"assignee_id": str(manager.id)}

    # Reassign to Member
    expect(owner_client.patch(assign_url, {"user_id": str(member.id)}, format="json"), 200)
    enquiry.refresh_from_db()
    assert enquiry.assigned_to_id == member.id

    e2 = EnquiryEvent.objects.filter(enquiry=enquiry, action=EnquiryEventAction.ASSIGNED).latest(
        "created_at"
    )
    assert e2.metadata == {
        "assignee_id": str(member.id),
        "previous_assignee_id": str(manager.id),
    }

    # Unassign
    expect(owner_client.patch(assign_url, {"user_id": None}, format="json"), 200)
    enquiry.refresh_from_db()
    assert enquiry.assigned_to_id is None

    e3 = EnquiryEvent.objects.filter(enquiry=enquiry, action=EnquiryEventAction.UNASSIGNED).latest(
        "created_at"
    )
    assert e3.metadata == {"previous_assignee_id": str(member.id)}


def test_every_allowed_status_transition(password: str) -> None:
    suffix = uuid.uuid4().hex[:8]
    owner, client, listing = _personal(password, suffix)

    # All allowed transitions:
    # NEW -> CONTACTED, NEW -> SPAM, NEW -> CLOSED
    # CONTACTED -> QUALIFIED, CONTACTED -> UNQUALIFIED, CONTACTED -> SPAM, CONTACTED -> CLOSED
    # QUALIFIED -> CONVERTED, QUALIFIED -> SPAM, QUALIFIED -> CLOSED
    # UNQUALIFIED -> CLOSED

    allowed_paths = [
        [EnquiryStatus.CONTACTED, EnquiryStatus.QUALIFIED, EnquiryStatus.CONVERTED],
        [EnquiryStatus.CONTACTED, EnquiryStatus.QUALIFIED, EnquiryStatus.SPAM],
        [EnquiryStatus.CONTACTED, EnquiryStatus.QUALIFIED, EnquiryStatus.CLOSED],
        [EnquiryStatus.CONTACTED, EnquiryStatus.UNQUALIFIED, EnquiryStatus.CLOSED],
        [EnquiryStatus.CONTACTED, EnquiryStatus.SPAM],
        [EnquiryStatus.CONTACTED, EnquiryStatus.CLOSED],
        [EnquiryStatus.SPAM],
        [EnquiryStatus.CLOSED],
    ]

    for path in allowed_paths:
        enquiry = _store(listing, contact_email=f"path-{uuid.uuid4().hex[:6]}@example.com")
        transition_url = f"{ENQUIRIES}{enquiry.id}/transitions/"
        current = EnquiryStatus.NEW
        for target in path:
            resp = expect(
                client.post(
                    transition_url,
                    {"status": target, "reason_code": "verified_lead"},
                    format="json",
                ),
                200,
            )
            assert resp["status"] == target
            enquiry.refresh_from_db()
            assert enquiry.status == target
            if target == EnquiryStatus.CLOSED:
                assert enquiry.closed_at is not None
            else:
                assert enquiry.closed_at is None

            event = EnquiryEvent.objects.filter(
                enquiry=enquiry,
                action=EnquiryEventAction.STATUS_CHANGED,
                metadata__to_status=target,
            ).first()
            assert event is not None
            assert event.metadata["from_status"] == current
            assert event.metadata["to_status"] == target
            assert event.metadata["reason_code"] == "verified_lead"
            # Ensure NO PII in metadata
            assert "contact_name" not in event.metadata
            assert "contact_email" not in event.metadata
            assert "message" not in event.metadata
            assert enquiry.contact_name not in str(event.metadata)
            assert enquiry.contact_email not in str(event.metadata)
            current = target


def test_forbidden_status_transitions_and_terminal_protection(password: str) -> None:
    suffix = uuid.uuid4().hex[:8]
    owner, client, listing = _personal(password, suffix)

    # 1. Skipped transition NEW -> QUALIFIED
    e1 = _store(listing)
    t1 = f"{ENQUIRIES}{e1.id}/transitions/"
    r = client.post(t1, {"status": EnquiryStatus.QUALIFIED}, format="json")
    assert r.status_code == 400
    assert error_detail(r) == ENQUIRY_TRANSITION_INVALID

    # 2. Terminal state protection: CLOSED, SPAM, CONVERTED cannot transition
    # Test CLOSED
    expect(client.post(t1, {"status": EnquiryStatus.CLOSED}, format="json"), 200)
    for forbidden in (EnquiryStatus.NEW, EnquiryStatus.CONTACTED, EnquiryStatus.CLOSED):
        resp = client.post(t1, {"status": forbidden}, format="json")
        assert resp.status_code == 400
        assert error_detail(resp) == ENQUIRY_TRANSITION_INVALID

    # Test SPAM
    e2 = _store(listing, contact_email="spam@example.com")
    t2 = f"{ENQUIRIES}{e2.id}/transitions/"
    expect(client.post(t2, {"status": EnquiryStatus.SPAM}, format="json"), 200)
    for forbidden in (EnquiryStatus.NEW, EnquiryStatus.CLOSED, EnquiryStatus.CONTACTED):
        resp = client.post(t2, {"status": forbidden}, format="json")
        assert resp.status_code == 400
        assert error_detail(resp) == ENQUIRY_TRANSITION_INVALID

    # Test CONVERTED
    e3 = _store(listing, contact_email="converted@example.com")
    t3 = f"{ENQUIRIES}{e3.id}/transitions/"
    expect(client.post(t3, {"status": EnquiryStatus.CONTACTED}, format="json"), 200)
    expect(client.post(t3, {"status": EnquiryStatus.QUALIFIED}, format="json"), 200)
    expect(client.post(t3, {"status": EnquiryStatus.CONVERTED}, format="json"), 200)
    for forbidden in (EnquiryStatus.CLOSED, EnquiryStatus.QUALIFIED, EnquiryStatus.NEW):
        resp = client.post(t3, {"status": forbidden}, format="json")
        assert resp.status_code == 400
        assert error_detail(resp) == ENQUIRY_TRANSITION_INVALID


def test_transaction_rollback_on_event_failure(password: str) -> None:
    suffix = uuid.uuid4().hex[:8]
    owner, client, listing = _personal(password, suffix)
    enquiry = _store(listing)

    transition_url = f"{ENQUIRIES}{enquiry.id}/transitions/"
    with patch(
        "apps.marketplace.services.enquiries.EnquiryEvent.objects.create",
        side_effect=IntegrityError("Simulated DB audit event failure"),
    ):
        with pytest.raises(IntegrityError):
            client.post(transition_url, {"status": EnquiryStatus.CONTACTED}, format="json")

    enquiry.refresh_from_db()
    # Enquiry must NOT have transitioned to CONTACTED because transaction rolled back
    assert enquiry.status == EnquiryStatus.NEW

    assign_url = f"{ENQUIRIES}{enquiry.id}/assignment/"
    with patch(
        "apps.marketplace.services.enquiries.EnquiryEvent.objects.create",
        side_effect=IntegrityError("Simulated DB audit event failure"),
    ):
        with pytest.raises(IntegrityError):
            client.patch(assign_url, {"user_id": str(owner.id)}, format="json")

    enquiry.refresh_from_db()
    # Enquiry must NOT have been assigned because transaction rolled back
    assert enquiry.assigned_to_id is None


def test_staff_does_not_get_implicit_bypass(password: str) -> None:
    suffix = uuid.uuid4().hex[:8]
    people, agency, listing, enquiry = _organization_enquiry(password, suffix, ("manager",))
    staff = staff_user(f"staff-{suffix}@example.com", password)
    staff_client = client_for(staff)

    assign_url = f"{ENQUIRIES}{enquiry.id}/assignment/"
    transition_url = f"{ENQUIRIES}{enquiry.id}/transitions/"

    # Staff is not a member of the organization -> 404
    assert (
        staff_client.patch(
            assign_url, {"user_id": str(people["manager"].id)}, format="json"
        ).status_code
        == 404
    )
    assert (
        staff_client.post(
            transition_url, {"status": EnquiryStatus.CONTACTED}, format="json"
        ).status_code
        == 404
    )


def test_assignment_does_not_create_read_or_management_access(password: str) -> None:
    suffix = uuid.uuid4().hex[:8]
    people, agency, listing, enquiry = _organization_enquiry(
        password, suffix, ("manager", "member")
    )
    owner_client = client_for(people["owner"])
    member = people["member"]
    member_client = client_for(member)

    # Assign enquiry to Member
    assign_url = f"{ENQUIRIES}{enquiry.id}/assignment/"
    expect(owner_client.patch(assign_url, {"user_id": str(member.id)}, format="json"), 200)

    # Verify Member still cannot read or manage enquiry
    detail_url = f"{ENQUIRIES}{enquiry.id}/"
    assert member_client.get(detail_url).status_code == 403
    assert member_client.get(ENQUIRIES).data["count"] == 0

    assert (
        member_client.patch(assign_url, {"user_id": str(member.id)}, format="json").status_code
        == 403
    )
    transition_url = f"{ENQUIRIES}{enquiry.id}/transitions/"
    assert (
        member_client.post(
            transition_url, {"status": EnquiryStatus.CONTACTED}, format="json"
        ).status_code
        == 403
    )


def test_http_method_restrictions_and_unexpected_fields(password: str) -> None:
    suffix = uuid.uuid4().hex[:8]
    owner, client, listing = _personal(password, suffix)
    enquiry = _store(listing)

    assign_url = f"{ENQUIRIES}{enquiry.id}/assignment/"
    transition_url = f"{ENQUIRIES}{enquiry.id}/transitions/"

    # Method not allowed:
    # GET, POST, DELETE on /assignment/ -> 405
    assert client.get(assign_url).status_code == 405
    assert client.post(assign_url, {"user_id": str(owner.id)}, format="json").status_code == 405
    assert client.delete(assign_url).status_code == 405

    # GET, PATCH, DELETE on /transitions/ -> 405
    assert client.get(transition_url).status_code == 405
    assert (
        client.patch(transition_url, {"status": EnquiryStatus.CONTACTED}, format="json").status_code
        == 405
    )
    assert client.delete(transition_url).status_code == 405

    # Unexpected fields on assignment
    resp = client.patch(
        assign_url,
        {"user_id": str(owner.id), "status": EnquiryStatus.CONTACTED},
        format="json",
    )
    assert resp.status_code == 400
    assert "Unexpected fields are not allowed." in resp.content.decode()

    # Unexpected fields on transition
    resp = client.post(
        transition_url,
        {"status": EnquiryStatus.CONTACTED, "closed_at": "2026-11-11T00:00:00Z"},
        format="json",
    )
    assert resp.status_code == 400
    assert "Unexpected fields are not allowed." in resp.content.decode()
