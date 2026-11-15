# RBAC architecture

Status: implemented in M3. M4 grants three property permissions from the same registry. Organization roles are evaluated only inside one organization. The registry is `apps.organizations.domain`.

## Roles

Stored values are lowercase. Labels used in this document match the API.

| Role | Rank | Meaning |
|---|---|---|
| `owner` | 50 | The single active owner. |
| `admin` | 40 | Operates the organization except ownership and organization status. |
| `manager` | 30 | Updates the profile, invites lower roles, and suspends lower members. |
| `member` | 20 | Reads the organization and its properties. Reserved so a later module can grant more than a viewer without a new role. |
| `viewer` | 10 | Reads the organization and its properties. Same grants as `member`. |

`member` and `viewer` are different ranks with the same permissions today. A manager can invite a viewer and cannot invite a manager.

## Permission matrix

| Permission | Owner | Admin | Manager | Member | Viewer |
|---|---|---|---|---|---|
| `organization.view` | yes | yes | yes | yes | yes |
| `organization.update` | yes | yes | yes | | |
| `organization.manage_settings` | yes | yes | | | |
| `organization.manage_members` | yes | yes | yes | | |
| `organization.invite_members` | yes | yes | yes | | |
| `organization.remove_members` | yes | yes | | | |
| `organization.manage_roles` | yes | yes | | | |
| `organization.view_audit` | yes | yes | yes | | |
| `organization.manage_billing` | yes | yes | | | |
| `organization.transfer_ownership` | yes | | | | |
| `organization.manage_status` | yes | | | | |
| `property.create` | yes | yes | yes | | |
| `property.update` | yes | yes | yes | | |
| `property.archive` | yes | yes | yes | | |
| `property.publish` | yes | yes | yes | | |
| `leads.view` | yes | yes | yes | | |
| `leads.manage` | yes | yes | yes | | |

`organization.update` covers display name, contacts, website, region, and address. `organization.manage_settings` covers legal name, type, registration reference, slug, and country. `organization.manage_status` lets the owner deactivate an active organization. It does not let the owner suspend or reactivate it. `organization.manage_billing` is reserved for a future billing module. M3 has no billing API.

`organization.transfer_ownership` is owner-only because ownership is a separate atomic operation, not a role patch.

## Reserved permissions

These names are registered and granted to nobody. A later module adds the grant in `ROLE_PERMISSIONS` when the module exists.

`listing.manage`, `leases.manage`, `finance.view`, `finance.manage`, `hospitality.manage`, `service_requests.manage`.

M5 grants `property.publish` to owner, admin, and manager. `listing.manage` stays reserved. `property.publish` lets those roles operate organization listings and accept organization mandates. It does not skip moderation, a listing mandate, or membership checks. Members and viewers do not receive it. Organization property reads still use an active membership and do not add a `property.view` permission. The object rules are in [PROPERTY_ARCHITECTURE.md](PROPERTY_ARCHITECTURE.md) and [LISTING_MANDATES.md](LISTING_MANDATES.md).

M6-B.3 grants `leads.view` and `leads.manage` to owner, admin, and manager. Members and viewers do not receive them. `property.publish` does not grant either name. An enquiry assignment does not grant them. An organization enquiry read requires an active account, a verified email, an active organization, an active membership, and one of those two permissions. A personal enquiry read requires the caller to be the recorded advertiser. The inbox queryset applies that scope on every request. A client-supplied organization id, advertiser id, or permission is not consulted.

M6-B.4 uses `leads.manage` for enquiry assignment and lifecycle transitions. For organization enquiries, the actor must hold `leads.manage` on an active membership in an active organization, and assignees must be active members in that same organization. For personal enquiries, the recorded personal advertiser retains authoritative management. Assignment does not create read access or transfer ownership, and staff receives no implicit bypass. All assignment and transition operations record append-only audit events under row locking and transactional rollback.

`GET /api/v1/organization-roles/` returns the role catalogue and this reserved list to any active authenticated user.

## Evaluation

Every organization-scoped operation checks, in this order:

1. The user is authenticated.
2. The account status is `active`.
3. The organization exists. A missing id is 404.
4. The user has a membership. No membership is 403, including when the caller is platform staff.
5. The organization status is `active`. A member of a non-active organization is 403.
6. The membership status is `active`.
7. The role grants the permission.
8. The object belongs to that organization. A membership or invitation id from another organization is 404.

The URL organization id is only a lookup key. `HasOrganizationPermission` performs this check, and the service repeats it under a row lock for writes. `is_staff` is not consulted by these checks.

An actor may invite or assign a role only when their rank is strictly greater. Equal ranks cannot manage each other. Nobody can grant themselves a role. The owner role is reached only by creating the organization or by ownership transfer.

## Adding a permission later

1. Add the constant to `Permission` if it is not already reserved.
2. Grant it from `ROLE_PERMISSIONS` for the roles that should have it.
3. Call `authorize(user, organization_id, permission)` in the service before reading or writing the business row.
4. Filter the queryset by the authorized organization.
5. Add an audit action if the operation changes authority or customer data.

Do not copy a role onto `identity.User`. Do not treat a client-supplied organization id as authorization.

## Platform staff

Staff and organization administrators are different. A staff user who is a viewer still has only viewer permissions. Staff may change organization status through `POST /api/v1/organizations/{id}/status/` without a membership. That response contains the id and the new status, not the organization profile. Django admin for these models is inspect-only. Identity admin remains the operator path for account suspension, and it still does not enforce TOTP.

## AI assistants

An AI feature must call the same services as the signed-in user. It uses that user's identity, membership, and permissions. It does not receive a broader role because it is an assistant. It reads only rows the service would return to that user. Consequential actions go through those services so the audit row records the user. Sensitive operations require the same explicit authorization as the API. An assistant must not query organization tables directly, invent a staff bypass, or skip `authorize`. M3 does not ship an assistant runtime.
