# Property architecture

Status: implemented in M4. Decisions are in [ADR — Property domain](decisions/ADR-property-domain.md). Location is in [LOCATION_ARCHITECTURE.md](LOCATION_ARCHITECTURE.md).

## Distinctions

These are separate facts. A later listing, mandate, or title check must not collapse them.

| Concept | M4 record | Meaning |
|---|---|---|
| Property | `property` | The physical asset. Lifecycle is draft, active, or archived. |
| Listing | `listing` in `apps.marketplace` | An offer that references `property.id`. See [MARKETPLACE_ARCHITECTURE.md](MARKETPLACE_ARCHITECTURE.md). |
| Ownership | `property_party` with role `owner` or `co_owner` | A claimed relationship. Status `claimed` is not legal title. |
| Management | `management_context` plus `property_access` | Who may operate the record inside GLITTERS. |
| Representation | `property_party` role `representative` | A claimed mandate. It does not grant API access by itself. |

An agency membership does not make the agency the legal owner. It does not authorize an agent to represent every property that organization manages. `registration_reference` and `phone_verified_at` are not read by property authorization.

```text
identity.User
    | created_by
    v
property ---- parent ----> property
    |                         |
    | management_context      +-- property_attribute
    | managing_organization?  +-- property_location --> address
    |                         +-- property_party
    +-- property_access       +-- property_event
```

## Tenancy

`management_context` is `personal` or `organization`.

- Personal requires `managing_organization` null. A check constraint rejects the other combination.
- Organization requires `managing_organization`. That foreign key means the organization operates the record. It does not mean the organization owns the land.
- `created_by` is always the user who inserted the row.
- Context is immutable in M4. There is no personal-workspace table.

This is the exception to the M3 rule that every business row has a non-null organization foreign key. The exception is safe only because personal visibility is an explicit `property_access` row, and list queries keep personal rows in their own branch. A `NOT IN` filter on a null organization id is unknown in SQL and would hide those rows.

Creating a personal property inserts a `manage` grant for the creator in the same transaction. That grant is operational access. It is not an owner party. The last personal `manage` grant cannot be revoked.

Organization children are visible through membership. Personal children copy the parent's active grants at creation and record `origin=inherited` with `inherited_from` pointing at the parent grant. Revoking a grant revokes those inherited copies, including grandchildren. A direct grant on a descendant is left in place. The last-manager guard applies to a direct personal revoke and is skipped for the cascade, so a personal child can be left without a manage grant. Copies are not rebuilt when a later grant is added on the parent.

An organization in Kenya may manage a property whose address is in another seeded country. `Organization.country` is not checked against the address country.

## Lifecycle

| Status | Allowed next status |
|---|---|
| `draft` | `active` |
| `active` | none through update |
| `archived` | none |

Archive is a separate operation and is terminal. `property.update` cannot set `archived`. Creating a row already archived is rejected. An active property needs a country and a street, an administrative area, or a locality. Coordinates stay optional. A parent with a draft or active child cannot be archived until those children are archived. Active records are not returned to draft.

The internal reference is `GLT-` plus random hex. Clients do not choose it.

## Types and hierarchy

Types: `residential`, `commercial`, `land`, `industrial`, `agricultural`, `mixed_use`, `hospitality`. Each type has a closed subtype list, including `other`. The pair is a database check.

| Role | Parent |
|---|---|
| `estate` | none |
| `standalone` | none |
| `building` | none, or an estate |
| `unit` | a building or an estate |

A child must share the parent's context and organization. The role rules make a cycle impossible. Updates also reject a property whose parent is itself, and they lock the property and the parent in primary-key order. `decide` runs before `select_for_update`, so a stranger does not lock a hidden row.

An estate is a development or collection. A building may stand alone. A unit is an apartment, suite, or plot inside a parent. A standalone property has no parent.

## Characteristics

Physical values are rows in `property_attribute`, one code per property. Each row holds exactly one of an integer, a decimal, or text. Areas are square metres. `land_use` is `residential`, `commercial`, `agricultural`, `industrial`, `mixed`, `conservation`, or `unknown`.

Codes in M4: `bedrooms`, `bathrooms`, `living_areas`, `floor_area_sqm`, `parking_spaces`, `floor_number`, `usable_floor_area_sqm`, `floors`, `plot_area_sqm`, `land_use`, `warehouse_area_sqm`, `building_area_sqm`. The catalogue says which property types may use each code. A code that does not belong to the current type is rejected. Replacing characteristics is a full replace of that property's rows. Changing type while stored codes would become invalid is rejected unless the same request replaces the characteristics.

Integers reject booleans, floats, and strings. Decimals are quantized to 0.01. There is no JSON document for the whole property.

## Parties and access

A party subject is a person or an organization. Roles: `owner`, `co_owner`, `manager`, `developer`, `representative`, `authorized_contact`.

| Status | API |
|---|---|
| `claimed` | Created by the API. |
| `pending_verification` | Allowed from `claimed`. |
| `rejected` | Allowed from an open claim. The row remains. |
| `revoked` | Its own operation. The row remains. |
| `verified` | Not writable. The database requires `verified_at` and a non-empty `verification_source`. |

`legal_title_verified` is true only when the stored status is `verified`. M4 never writes that status. There is no land-registry integration. GLITTERS does not verify legal title.

An organization party does not give that organization's members access. A person receives access only when the request includes `access_capability` `view` or `manage`. Open claims are unique per property, subject, and role. Party and access changes run in one transaction and append `property_event`.

`view` can read the property and cannot see exact coordinates, the access list, or the audit log. `manage` can update, archive, manage parties, and read coordinates and audit while the organization, when there is one, is active. A grant does not authorize creating other properties for that organization.

## Authorization

Every property operation passes through this order:

1. Authentication.
2. Active account.
3. Personal access, or organization membership or an explicit grant.
4. Organization lifecycle, when the property is organization-managed.
5. Membership lifecycle, when a membership row exists.
6. The required permission, or the `manage` capability.
7. The object check inside `decide`.
8. The transactional service.

A missing property, a personal property without a grant, and an organization property with neither a membership nor a grant are 404 with `Not found.` A suspended organization is 403, `This organization is not active.` A membership that exists and is not active is 403, `This membership is not active.`, including when a grant also exists. `is_staff` is not a property role.

Organization reads require an active membership. They do not use a `property.view` permission. Members and viewers can read every property of an active organization. Hiding one record from colleagues is not implemented.

| Permission | Owner | Admin | Manager | Member | Viewer |
|---|---|---|---|---|---|
| `property.create` | yes | yes | yes | | |
| `property.update` | yes | yes | yes | | |
| `property.archive` | yes | yes | yes | | |
| `property.publish` | yes | yes | yes | | |

`property.publish` authorizes organization listing operations. It does not publish a listing by itself and it does not replace a mandate. Coordinate disclosure, audit reads, and party writes follow `property.update` or a `manage` grant. Archive follows `property.archive` or a `manage` grant. Access responses include `origin` and `inherited_from`.

List filters for another organization return an empty page. They do not return 403, because an empty accessible set is the same shape as a filter that matches nothing.

## Audit

`property_event` is append-only. Save, delete, and queryset update or delete raise. The event copies `managing_organization_id`, which is null for a personal property. It does not also write `organization_event`.

Actions: `property.created`, `property.updated`, `property.archived`, `property.characteristics_updated`, `property.participant_added`, `property.participant_updated`, `property.participant_revoked`, `property.access_granted`, `property.access_revoked`.

Metadata records `coordinates_updated` as a boolean. Cleaning drops latitude, longitude, coordinates, geometry, and secret keys before insert.

## Marketplace and AI

A listing references `property.id`. One property can have many listings over its life. The property row still has no price, deposit, commission, availability, or booking state. Property status and listing status stay different columns in different tables.

Later AI features can use structured attributes, the location chain, parties, and `property_event` for semantic search, recommendations, image-assisted categorization, indicative valuation, location intelligence, and data-quality checks. Those features must call the same services as the signed-in user. Listing descriptions may be marked `ai_assisted` and cannot be submitted until a person accepts them. M5 does not add embeddings, a vector database, or an accuracy claim.

## API

Routes, filters, and examples are in [V1.md](../api/V1.md). Unsafe property views opt back into CSRF. Errors stay `{errors, request_id}`. Pagination is 25 per page, with `page_size` up to 100.
