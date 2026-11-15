# Organization architecture

Status: implemented in M3 on 2026-11-11. The decision record is [ADR — Organization and RBAC](decisions/ADR-organization-and-rbac.md). Roles and permissions are in [RBAC_ARCHITECTURE.md](RBAC_ARCHITECTURE.md). Isolation rules are in [MULTI_TENANCY_ARCHITECTURE.md](MULTI_TENANCY_ARCHITECTURE.md).

## People and businesses

`identity.User` remains the person who can authenticate. Buyers, tenants, guests, and sellers use that account directly. The platform does not require them to create an organization.

An organization is a business the platform can authorize: a real estate agency, developer, property manager, landlord business, hospitality operator, professional service provider, or contractor. One person may belong to several organizations. The role is stored on the membership, never on the user.

## Organization

Table `organization`. UUID primary key.

| Field | Rule |
|---|---|
| `legal_name`, `display_name` | Required. Whitespace is collapsed. |
| `slug` | Public, unique, lowercase `[a-z0-9-]`. Derived from the display name when omitted. |
| `organization_type` | `agency`, `developer`, `property_manager`, `landlord`, `hospitality`, `professional_service`, `contractor`. |
| `registration_reference` | Optional label, maximum 64 characters, not unique. Storing it does not verify legal existence. |
| `contact_email` | Optional. Stored with the identity email normalizer. |
| `contact_phone` | Optional E.164. Kenya-style local numbers are normalized. This is a business contact, not proof of handset ownership. |
| `website` | Optional `http` or `https` URL. |
| `country` | ISO 3166-1 alpha-2. Default `KE`. Not a Kenya-only constraint. |
| `region`, `address_line` | Optional operating location text. |
| `status` | Lifecycle below. |
| `created_at`, `updated_at` | Timestamps. |

Self-service creation starts at `active` and creates an active owner membership in the same transaction. `pending_verification` is a later staff review state. It is not implied by a registration reference.

### Organization status

| From | Allowed next | Who |
|---|---|---|
| `pending_verification` | `active`, `rejected` | Platform staff |
| `active` | `suspended`, `deactivated`, `pending_verification` | Staff. The owner may only move `active` to `deactivated`. |
| `suspended` | `active`, `deactivated` | Staff |
| `deactivated` | `active` | Staff |
| `rejected` | none | Terminal |

Staff means `is_staff` or `is_superuser`, with an active account. API staff calls still require confirmed TOTP through the identity middleware. A staff status change returns only `{id, status}`. It does not grant membership, permissions, or the organization profile.

A non-active organization grants no membership permission. The caller's `GET /api/v1/me/memberships/` still lists it so the person can see why access stopped.

## Membership

Table `organization_membership`. One row per `(organization, user)`. The user foreign key uses `settings.AUTH_USER_MODEL`.

| Field | Rule |
|---|---|
| `role` | `owner`, `admin`, `manager`, `member`, `viewer`. |
| `status` | `invited`, `active`, `suspended`, `removed`. |
| `source` | `creator`, `invitation`, or `platform`. The API does not create `platform` rows. |
| `joined_at` | Set when the membership becomes active. Required by the database for `active`. |

There is at most one active owner per organization (`organization_one_active_owner`). The service keeps that count at one after creation. History lives in `organization_event`, not in extra membership rows.

### Membership transitions

| From | To | Operation |
|---|---|---|
| `invited` | `active` | Accept a valid invitation. |
| `invited` | `removed` | Revoke that invitation. |
| `active` | `suspended` | A higher-ranked member with `organization.manage_members`. |
| `active` or `suspended` | `removed` | `organization.remove_members`, or self-service leave when the member is not the owner. |
| `suspended` | `active` | `organization.manage_members`, or acceptance of a new valid invitation. |
| `removed` | `invited` | A new invitation for the same person. |
| `removed` | `active` | Acceptance of that invitation. |

A member cannot change their own role. An actor can assign or manage only a role of strictly lower rank. `owner` is not an assignable invitation or patch role.

## Invitation

Table `organization_invitation`.

| Field | Rule |
|---|---|
| `email` | Normalized with the identity email rule. |
| `role` | Any role except `owner`. The database rejects `owner`. |
| `invited_by` | The acting member. Null if that user is later removed. |
| `token_hash` | SHA-256 of a `secrets.token_urlsafe(32)` value. The raw token is emailed once and is not stored, logged, or returned by the API. |
| `expires_at` | Seven days from create or resend. |
| `accepted_at`, `revoked_at` | Set when those transitions happen. |
| `status` | `pending`, `accepted`, `revoked`, `expired`. |

One `pending` row is allowed per organization and email. Resend replaces the hash and extends the expiry, which invalidates the previous token. Expiry is evaluated when an invitation is created, read, resent, revoked, accepted, or inspected. There is no Celery sweep.

Acceptance is one transaction:

1. The organization row is locked, then the invitation row.
2. A pending invitation past `expires_at` is marked `expired` and that write is committed before the error is returned.
3. The caller must be an active user whose verified email equals the invited email.
4. The organization must be `active`.
5. An already-active member is rejected with 409 and the role is left unchanged. The token stays pending.
6. A suspended, removed, or invited membership is activated at the invited role. A missing membership is created.
7. The invitation is marked `accepted`. Replay of that token fails.

An email mismatch returns 403 `This invitation does not match the signed-in account.` and does not reveal the invited address or consume the token. Inspecting a token returns the organization display name, role, status, expiry, and whether the signed-in email matches. It does not return the invited email or the token.

If the invited address already belongs to a user, the membership is set to `invited` immediately. An unknown address has only the invitation until acceptance.

## Invariants

1. Every organization has exactly one membership with `role=owner` and `status=active` after a successful create or transfer.
2. The database allows at most one such owner. The service refuses to remove, suspend, or demote that owner except through transfer.
3. A user has at most one membership row per organization.
4. At most one pending invitation exists per organization and normalized email.
5. Invitation acceptance is one-time. Accepted, revoked, and expired invitations cannot be accepted.
6. Raw invitation tokens are never stored or written to audit metadata.
7. Acceptance requires an active account, `email_verified_at`, and an exact normalized email match.
8. Acceptance never changes the role of an already-active member.
9. A suspended membership grants no permission.
10. A non-active organization grants no membership permission.
11. Platform staff may change organization status without a membership. That path does not grant organization data or organization permissions.
12. A role in one organization grants nothing in another.
13. `is_staff` is not an organization role and does not satisfy organization permission checks.
14. A member cannot change their own role.
15. An actor cannot assign a role of equal or higher rank, and cannot assign `owner` by invitation or role update.
16. Ownership transfer is one transaction. The acting owner becomes `admin` before the target becomes `owner`, so the unique index never sees two active owners. The target must be an active member of the same organization whose account is active.
17. Ownership and role changes are audited.
18. A registration reference does not verify legal existence.

Writes that must remain after a failure, such as marking an invitation expired, are committed before the error is raised. PostgreSQL row locks are taken on the organization before membership or invitation rows.

## API

The routes are under `/api/v1/` and are listed in [the v1 contract](../api/V1.md). Collection responses use page-number pagination, 25 by default, with `page_size` up to 100. Errors use `{errors, request_id}`. Unsafe methods opt back into CSRF because DRF marks handlers CSRF-exempt.

Django admin registers these models as inspect-only. Status changes and membership changes go through the API so they are authorized and audited.

## Audit

Table `organization_event` is append-only. Updates and deletes raise. Each row stores the actor, organization, action, object type, object id, request id, client IP from `REMOTE_ADDR`, timestamp, and JSON metadata. Metadata keys named token, password, secret, authorization, or cookie are dropped. Actions use names such as `organization.created`, `invitation.accepted`, `membership.role_changed`, and `ownership.transferred`.
