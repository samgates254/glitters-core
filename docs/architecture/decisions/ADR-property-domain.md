# ADR — Property domain

- Status: accepted
- Date: 2026-11-11
- Milestone: M4
- Builds on: [ADR — Organization and RBAC](ADR-organization-and-rbac.md), [ADR — Location model](ADR-location-model.md)

This decision is accepted before the M4 migrations.

## Context

GLITTERS must record physical real estate without forcing every person to create an organization. An individual landlord or seller needs a private record. An agency, developer, or property manager needs records the organization operates. Those are different from legal ownership, from a marketplace listing, and from a mandate to represent someone else's property.

M3 says later business rows should carry a non-null organization foreign key. A nullable organization column without a second rule would make personal rows readable by accident, and it would treat the managing organization as if it were the owner. M4 does not take that shortcut.

The permission registry already names `property.create`, `property.update`, `property.publish`, and `property.archive`. None of those names were granted in M3.

## Decision

### Tenancy

A property has a required `created_by` user and a `management_context` of `personal` or `organization`.

- `personal` requires `managing_organization` to be null. The database rejects any other combination.
- `organization` requires `managing_organization`. The organization manages the record. It is not recorded as the legal owner by that foreign key.
- Context cannot be changed later. Moving a personal record into an organization is a later transfer, not an edit.
- There is no personal-workspace table. The property row and an explicit access grant are the personal boundary.

Personal visibility is an active row in `property_access` for that user. Creating a personal property inserts a `manage` grant for the creator in the same transaction. That grant is operational access. It is not an ownership claim. A personal property with no remaining `manage` grant cannot be produced through the API: the last `manage` grant cannot be revoked.

Organization visibility requires an active account, an active organization, and an active membership. Writes also require the permission below. A URL or body id is only a lookup key. `is_staff` is not a property role. `registration_reference` and `phone_verified_at` are not consulted.

A non-member with no grant receives 404. A member of a suspended organization, or a suspended or removed membership, receives 403 and no property data. An explicit grant does not bypass a non-active organization or a non-active membership in that organization.

Members and viewers of an active organization can read its property records. They cannot create, change, or archive them. Intra-organization hiding of individual records is not part of M4.

An explicit `property_access` grant can add a person who is not a member. `view` can read the record and cannot see exact coordinates. `manage` can update, archive, and manage participants while the organization is active. A grant never authorizes creating other properties for that organization, and it never authorizes every property the organization manages.

Subordinate personal properties copy the parent's active grants at creation. M4 did not cascade a later parent revocation. M5 records provenance and revokes inherited copies. Organization children rely on membership, not copied grants.

### Permission matrix

Granted in this milestone, from `ROLE_PERMISSIONS` only:

| Permission | Owner | Admin | Manager | Member | Viewer |
|---|---|---|---|---|---|
| `property.create` | yes | yes | yes | | |
| `property.update` | yes | yes | yes | | |
| `property.archive` | yes | yes | yes | | |

`property.publish` stays in `FUTURE_PERMISSIONS` and is granted to no role. Listing publication is a later milestone. Members and viewers are not given every property permission. No second permission system is introduced.

Organization reads use active membership. They do not require a new `property.view` name. Coordinate, audit, and participant changes require `property.update` or a personal `manage` grant. Archive requires `property.archive` or a personal `manage` grant.

### What a property is

A property is the canonical physical asset. Its lifecycle is `draft`, `active`, or `archived`. Listing, offer, and transaction states are not property states. Archive is terminal in M4 and uses its own operation, so `property.update` cannot archive. A parent with draft or active children cannot be archived until those children are archived.

An active property needs a country and at least a street, an administrative area, or a locality. Coordinates remain optional.

The type and subtype are a closed pair, enforced by a check constraint. Categories are residential, commercial, land, industrial, agricultural, mixed-use, and hospitality. Physical characteristics are rows in `property_attribute`, one code per property, with a single integer, decimal, or text value. The code catalogue says which types may use it, the unit, and the bounds. There is no JSON document for the whole property. Areas are square metres. `land_use` is a small vocabulary, not free text.

### Hierarchy

`hierarchy_role` is `estate`, `building`, `unit`, or `standalone`.

- An estate or a standalone property has no parent.
- A building's parent is empty or an estate.
- A unit's parent is a building or an estate.
- A unit cannot contain another property.
- A child must share the parent's management context and managing organization.

Those role rules make a cycle impossible. Updates lock the property and the parent in primary-key order and check the rules again under the lock. A property cannot be its own parent.

An estate is a development or collection. A building may stand alone or sit in an estate. A unit is an apartment, suite, or plot inside a parent. A standalone property is a single asset with no parent and no children.

### Parties

`property_party` records a claimed relationship for a person or an organization: owner, co-owner, manager, developer, representative, or authorized contact. Creating one does not verify legal title and does not by itself grant API access.

Statuses are `claimed`, `pending_verification`, `verified`, `rejected`, and `revoked`. The API can create only `claimed`. It can move a claim to `pending_verification`, `rejected`, or `revoked`. It cannot set `verified`. A database check requires `verified_at` and a non-empty `verification_source` before `verified` can be stored. M4 has no process that writes those fields and no land-registry integration. `legal_title_verified` in the API is true only when the stored status is `verified`.

Organization parties do not grant that organization's members access to the property. A person receives access only when the caller sends an explicit `access_capability` of `view` or `manage`.

Party and access changes run in one transaction and write `property_event`. Revoked and rejected rows stay as history. Open claims are unique per property, subject, and role.

### Marketplace and AI

A future listing will reference `property.id`. The property row does not contain price, deposit, commission, availability, or booking state. Several listings over the life of one property remain possible. M4 does not create a listing table.

AI callers must use these services with the same user. There is no assistant bypass, no embedding store, and no accuracy claim. Structured attributes, the location chain, parties, and `property_event` are the later inputs for search, recommendations, image categorization, indicative valuation, location intelligence, and data-quality checks.

## Consequences

- Personal records stay invisible to other users and to the creator's organizations unless a grant or a managing organization is added by a later, explicit operation.
- Agency membership does not authorize representation of every property the agency manages.
- M4 left invitation acceptance unchanged. M5 refuses to restore a removed or suspended membership from an old token. Property services still require the membership to be active at the time of the call, and they do not treat a membership as title or as a mandate.
- Tenant isolation remains application-level. PostgreSQL row-level security is not added.
- M5 later granted `property.publish` to owner, admin, and manager and added inherited-grant revocation. This M4 decision left the permission ungranted and did not cascade subordinate grants. The current rules are in [LISTING_MANDATES.md](../LISTING_MANDATES.md).

## Invariants

One management context, matching the organization column. Personal rows are visible only through `property_access`. Organization rows are visible only through an active membership or an explicit grant while that organization is active. The managing organization is not the owner. A party role is not verification. Exact coordinates are not in list, search, audit, or admin payloads. Property status is not listing status.
