# ADR — Organization and RBAC

- Status: accepted
- Date: 2026-11-11
- Milestone: M3
- Builds on: [ADR — Identity and authentication](ADR-identity-authentication.md)

## Context

M2 left `identity.User` as the only user model. Buyers, tenants, guests, and sellers must be able to use the platform without incorporating a business. Agencies and the other business types need several people, with different authority, and one person may work for more than one business.

Putting a role on the user would make that role global. Using `is_staff` as an organization role would mix platform administration with tenant administration.

## Decision

Add `apps.organizations` beside identity. Identity does not import it. Memberships reference `settings.AUTH_USER_MODEL`.

Self-service organization creation starts as `active` and creates the creator as the only active owner. `pending_verification`, `suspended`, `deactivated`, and `rejected` are real lifecycle states. A registration reference is stored as text and is not evidence that a registrar issued it or that the business exists. `pending_verification` is applied by platform staff, not by typing a number.

Membership is one row per user and organization. Status is `invited`, `active`, `suspended`, or `removed`. The partial unique index `organization_one_active_owner` allows at most one active owner. Transfer demotes the acting owner to `admin` and then promotes the target, inside one transaction, so the index is never asked to hold two owners.

Invitations store a SHA-256 hash of a high-entropy token, expire after seven days, and accept once. Acceptance requires the caller's verified email to match. It does not change an already-active member's role. The owner role cannot be invited.

Permissions live in one registry. M3 grants only `organization.*` permissions, including `organization.transfer_ownership` and `organization.manage_status` because the ownership and deactivation rules need distinct operations. Later permissions are registered and granted to no role. `member` and `viewer` both have `organization.view` and different ranks, so a future module can split them without a new role migration.

Authorization is organization-scoped. Staff may change organization status without a membership and receive only the id and status back. They do not gain organization permissions. Django admin for these four models is inspect-only.

The database stays the existing PostgreSQL database. Tenant isolation is a foreign key plus service checks, documented in [MULTI_TENANCY_ARCHITECTURE.md](../MULTI_TENANCY_ARCHITECTURE.md).

## Consequences

- Future business tables need an `organization` foreign key and a call to `authorize` with a permission from this registry.
- An organization can be left `active` before any human review. Staff can move it to `pending_verification` or `suspended`.
- Deactivating an organization is an owner action. Turning it back on is a staff action.
- There is exactly one active owner. A second owner would require a new decision and a migration.
- Invitation expiry is lazy. A pending row past its expiry blocks a duplicate until the next create, which marks it expired inside the same transaction.
- AI features, billing, and Channels are not part of this decision beyond the permission names and the access rules they must reuse. M4 grants `property.create`, `property.update`, and `property.archive` from this registry and records personal properties without an organization. That exception is [ADR — Property domain](ADR-property-domain.md). M5 grants `property.publish` to owner, admin, and manager. `listing.manage` stays ungranted.

## Invariants

The enforced list is in [ORGANIZATION_ARCHITECTURE.md](../ORGANIZATION_ARCHITECTURE.md). The short form is: one active owner, one membership row per user and organization, one pending invitation per email, hashed one-time tokens, verified email match, no self-promotion, no cross-organization grant, and no staff bypass of membership checks except the status operation described above.
