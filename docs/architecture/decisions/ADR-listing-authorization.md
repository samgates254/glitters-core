# ADR — Listing authorization

- Status: accepted
- Date: 2026-11-11
- Milestone: M5
- Builds on: [ADR — Marketplace engine](ADR-marketplace-engine.md), [ADR — Property domain](ADR-property-domain.md)

This decision is accepted before the M5 marketplace migration.

## Context

M4 can record a claimed owner, co-owner, manager, developer, representative, or authorized contact. That claim is not a verified title, and an organization party does not grant members access to the property. M3 membership likewise does not mean every member may advertise every property the organization manages.

M5 needs an explicit authority to advertise. Using the claim, or using membership alone, would let a disputed party or a viewer publish an offer. It would also invite the product to talk about that row as if GLITTERS had checked the land registry. It has not.

`property.publish` was reserved in M3 and left ungranted in M4. Organization publishing now needs that permission, without turning it into a bypass of moderation or of the mandate.

## Decision

### Listing mandate

`listing_mandate` is the advertising authority for one property. It names:

- the property
- one principal, either a person or an organization
- one representative, either a person or an organization
- the granting user
- a listing scope of one listing type or `any`
- `valid_from` and an optional `valid_until`
- a private evidence label, which is not legal proof and is not copied into events or public listings
- a status: draft (unused by the API), pending acceptance, active, rejected, revoked, or expired

`self_declared` is true only when the principal and the representative are the same person or the same organization. The database rejects any other combination.

A self-declaration becomes active immediately. A third-party mandate starts at pending acceptance. The representative user accepts or rejects it. For an organization representative, an active member with `property.publish` accepts or rejects it.

The grantor must already be allowed to update the property (`decide().can_update`). A party claim is not enough. The property row is locked inside the creation transaction, and an archived property is refused.

Default principal is `organization` when the actor can speak for the managing organization, which means an active publisher membership on an organization-managed property. Otherwise the default is `self`. `principal: organization` is refused without that authority. A personal `self` principal is the actor even on an organization-managed property. That is an operational self-declaration. It is not organization authority and it is not a title.

### What the mandate is not

The API returns `authority` of `self_declaration` or `representation`, plus a notice:

- Self-declaration: this is a declaration of authority to advertise. GLITTERS has not verified legal title.
- Representation: the mandate authorizes marketplace representation within its scope. It is not proof of legal ownership. GLITTERS has not verified legal title.

There is no `legal_title_verified` flag on a mandate. Account status, organization status, `registration_reference`, and `phone_verified_at` are not title, business-registration, or licence verification. Moderation does not add those claims.

### Enforcement

A listing may be submitted, approved, or resumed only when all of the following hold:

- the mandate belongs to that property
- the mandate is active and in force (`valid_from` is today or earlier, and `valid_until` is empty or today or later)
- the listing type is inside the scope
- the representative person or organization is the advertiser side of the listing
- the advertiser account is active and the email is verified
- when the representative is an organization, that organization is active and the advertiser's membership is active and includes `property.publish`

Revocation of an active or pending mandate withdraws published, paused, and pending-review listings on that mandate in the same transaction. Drafts stay drafts. The withdrawal reason is `mandate_revoked`.

If `valid_until` is before today, the next publication check or listing creation persists `expired`, records `mandate.expired`, withdraws the same non-draft public states with reason `mandate_expired`, and blocks publication. A future `valid_from` can be stored as active and is not yet in force.

An unrelated caller who cannot see the mandate receives 404, including on revoke.

### Organization permission

`property.publish` is granted to owner, admin, and manager. Member and viewer do not receive it. `listing.manage` stays reserved and is not granted.

`property.publish` lets an active member operate organization listings and accept organization mandates. It does not:

- publish without staff approval
- skip the mandate
- skip an active property
- skip an active account, organization, or membership
- apply to a personal property

Personal advertisers use their M4 manage grant plus a mandate. Managing a property is still not legal ownership.

### Invitation hardening carried into this milestone

Acceptance activates a membership only when there is no row, or the row is `invited`. An active membership stays a 409. A suspended or removed membership returns 403 and is not restored by an old token.

Sending a new invitation is a separate decision. It may move a suspended, removed, or invited row to `invited`. The person is not active until they accept that new invitation. Suspending or removing a member revokes pending invitations for that email. `reactivate_membership` remains the explicit staff-or-manager path for a row that is still suspended. After a fresh invitation has moved the row to `invited`, reactivation no longer applies until acceptance.

### Access provenance carried into this milestone

`property_access.origin` is `direct` or `inherited`. A direct grant has no `inherited_from`. An inherited grant points at the parent grant it was copied from. Child personal properties still copy parent grants at creation. Revoking a grant revokes the inherited copies, including grandchildren through that chain, and does not revoke a direct grant on a descendant. The last-manager guard applies to a direct personal revoke and is skipped for that cascade, so a personal child can be left with no manage grant. Replacing a parent grant revokes the old row and therefore its copies. Copies are not rebuilt until a later child is created. A parent property cannot be deleted while a child grant references its grant. Properties are archived, not deleted.

## Consequences

- An agency advertises a third-party property only after the representative accepts the mandate.
- A landlord advertises a personal property with a self-declaration and does not have to create an organization.
- Suspending the advertising organization, or the advertiser's membership, hides the public listing without withdrawing the row. Publication fails until the membership is eligible again. A colleague with `property.publish` does not become the recorded advertiser. They withdraw and create a new listing.
- Old invitation tokens cannot silently restore a removed or suspended member.

## Not chosen

- Treating `property_party` as advertising authority.
- Treating every organization member as a publisher.
- A land-registry or title-verification integration.
- Comparing invitation `created_at` with membership `updated_at` to guess which token is fresh.
- PostgreSQL row-level security. Isolation stays in the application, as in M3 and M4.
