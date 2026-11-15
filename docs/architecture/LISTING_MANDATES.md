# Listing mandates

Status: implemented in M5. A mandate authorizes marketplace representation inside its scope. It is not proof of legal ownership. GLITTERS does not verify land title and does not call a land registry.

## Who may advertise

An individual advertises a property they can update by creating a self-mandate. The principal and the representative are that person. The mandate becomes `active` immediately and is marked `self_declared`. The API returns:

`This is a self-declaration of authority to advertise. GLITTERS has not verified legal title.`

An organization that can update an organization-managed property, through an active member with `property.publish`, may grant a self-mandate to that same organization. It also becomes active immediately.

A third party starts at `pending_acceptance`. The representative user accepts or rejects it. For an organization representative, an active member with `property.publish` accepts or rejects it. The notice is:

`This mandate authorizes marketplace representation within its scope. It is not proof of legal ownership. GLITTERS has not verified legal title.`

A `property_party` claim is not this mandate. Organization membership is not this mandate. Managing a property is not legal ownership. A person who claims to be the owner does not receive a verified-owner badge.

The default principal is `organization` when the actor can speak for the managing organization, and `self` otherwise. `principal: "organization"` is refused without that authority. A personal `self` principal remains the actor even on an organization-managed property. That is an operational self-declaration, not organization authority.

Grantor authority is `decide().can_update`. It is not a party claim.

## Fields

- Property, protected.
- Principal person or principal organization, exactly one.
- Representative person or representative organization, exactly one.
- Granting user and, when accepted, the accepting user.
- Scope: one listing type or `any`.
- `valid_from`, and optional `valid_until` on or after `valid_from`.
- Status.
- `self_declared`, only when the principal and the representative are the same person or the same organization.
- Evidence label, at most 200 characters, no newlines. It is not copied into events or the public listing API. Participants can see it on the mandate payload.
- Acceptance, rejection, and revocation timestamps.

## States

`draft` exists on the model and is unused by the API. The API uses `pending_acceptance`, `active`, `rejected`, `revoked`, and `expired`.

A future `valid_from` may be stored as `active`. It is not in force until that date. Publication and submit refuse it. Draft creation allows it.

## Revocation and expiry

Revoke is available to the granting user, the principal user, the representative user, a publisher member of the principal or representative organization, or a caller who can currently update the property. Unrelated callers receive 404.

Revoking an active or pending mandate sets `revoked` and, in the same transaction, withdraws published, paused, and pending-review listings on that mandate. The reason is `mandate_revoked`. Drafts stay drafts.

If `valid_until` is before today and the status is still `active`, the next listing create, renew, or advertiser transition persists `expired`, records `mandate.expired`, withdraws those same listings with reason `mandate_expired`, and blocks publication. That expiry write uses its own transaction so a later 409 does not roll it back.

## Publication checks

A listing may be submitted, approved, or resumed only when:

- The mandate belongs to that property.
- The mandate is `active` and in force today.
- The listing type is inside the scope.
- The representative person or organization is the advertiser side of the listing.
- The advertiser account is active and the email is verified.
- When the representative is an organization, that organization is active and the advertiser's membership is active and includes `property.publish`.

Suspending the advertising organization, or the advertiser's membership, hides the public listing without withdrawing the row. A colleague with `property.publish` may pause it. Resume, submit, and approve fail until the recorded advertiser is eligible again. The colleague does not become the recorded advertiser. They withdraw and create a new listing.

## Organization permission

`property.publish` is granted to owner, admin, and manager. Member and viewer do not receive it. `listing.manage` stays reserved.

`property.publish` lets an active member operate organization listings and accept organization mandates. It does not skip moderation, the mandate, an active property, an active account, an active membership, or an active organization. Personal publishers use a manage grant plus a mandate. They do not need `property.publish`.

## Invitation hardening carried into M5

Accepting an invitation activates a membership only when that membership is absent or `invited`. An active membership stays active and the accept returns 409. A suspended or removed membership is not restored by an old token. The response is 403: `This membership needs a new invitation before it can be restored.`

A fresh invitation may move a suspended, removed, or already-invited membership back to `invited`. It does not make the membership active. The person must accept the new token. Suspend and remove revoke pending invitation tokens for that email. `reactivate_membership` is unchanged and applies only while the membership is still suspended. After a fresh invitation moves it to `invited`, reactivation no longer applies.

## Access provenance carried into M5

`property_access.origin` is `direct` or `inherited`. A direct grant has `inherited_from` null. An inherited grant points at the parent grant it was copied from. Personal children still copy parent grants only at creation. Revoking a grant revokes inherited copies, including grandchildren, and does not revoke a direct grant on a descendant. The last-manager guard applies to a direct personal revoke and is skipped for the cascade, so a personal child can be left with no manage grant. Replacing a parent grant revokes its copies. Copies are not rebuilt until a later child is created. Properties are archived, not deleted. The parent property cannot be deleted while a child grant references its grant.
