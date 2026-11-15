# Multi-tenancy architecture

Status: shared-database conventions for M3 and later modules. M4 adds a personal-property exception. M5 listings follow the property's management context and, when an organization advertises, that organization's membership. GLITTERS stays one PostgreSQL database. There is no database per tenant and no microservice split.

## Isolation model

An organization is the tenant boundary for business data. `identity.User` is global. A membership is the only link between a user and an organization. A role on that membership applies only to that organization.

The presence of an organization id in a URL, body, cache key, or task argument is not authorization. The service loads the row and calls `authorize` before trusting it.

## Personal properties

M4 property records may be personal. `management_context` is required. A check constraint keeps `managing_organization` null for `personal` and non-null for `organization`. Personal visibility is an active `property_access` row, not a null organization id that other users can query. The managing organization is not the legal owner.

List queries must keep the personal branch separate. Applying `managing_organization_id NOT IN (...)` to a null organization id is unknown in SQL and would drop personal rows.

Organization-managed properties still follow the rules below. A grant on one property does not authorize the rest of that organization's portfolio. Details are in [PROPERTY_ARCHITECTURE.md](PROPERTY_ARCHITECTURE.md).

## Listings

A personal listing has a null `advertising_organization`. The publisher list keeps that branch separate: the advertiser matches the caller, and the organization id is null. A `NOT IN` filter on a null organization id would hide those rows.

An organization listing names the representative organization. The publisher list includes it only when the caller has an active membership in an active organization and the role includes `property.publish`. A stranger receives 404 for one listing and an empty page for the collection. A member without `property.publish` receives 403 for one listing. Suspending the organization or the advertiser's membership hides the public row. It does not move the listing to another organization.

Public discovery does not list every property. It lists eligible published listings. There is no PostgreSQL row-level security policy on `listing`.

## Conventions for later business records

- Put a non-null foreign key to `organization` on the business row, unless an accepted ADR defines another tenancy check the way M4 does for personal properties. Use `on_delete=PROTECT` for records that must survive an accidental organization delete. M3's own membership, invitation, and audit rows cascade because they have no meaning without the organization.
- Name the column `organization`. Do not store the tenant only in a JSON blob.
- Selectors and list queries start from the authorized organization: `Model.objects.filter(organization=membership.organization)`.
- Object reads use the organization and the object id together. A hit in another organization is 404.
- Services call `authorize(user, organization_id, permission)` even when a DRF permission class already did. Writes lock the organization row with `select_for_update` before locking child rows.
- Do not filter business rows by the current user alone when the row belongs to an organization. Membership is the filter.
- Platform staff do not skip these filters. A staff-only operation, such as organization status, is a separate service with its own audit event.

## Background work

Celery has no application tasks in M3. When a task is added it must receive the organization id as an explicit argument, load the organization inside the task, and refuse to run if the organization is not in the expected status. It must not read "the current user" from a thread-local that a worker does not have. Audit events inside the task set `organization` and leave `actor` null unless a user id was passed for a reason that is still true.

## Audit and cache

Audit rows always store `organization`. Metadata must not contain tokens, passwords, or raw invitation secrets. `request_id` is copied from the HTTP request when the action came from the API.

Cache keys for organization data use `apps.organizations.domain.cache_key`:

```text
org:{organization_id}:{suffix}
```

The prefix is `glitters` at the cache backend, then this key. A key without the organization id must not hold tenant data. Cache is not authorization. Callers authorize before reading or writing it. M3 does not cache permissions, so a role change cannot be served from a stale grant.

## API context

DRF resolves the organization from the URL kwarg `organization_id` after authentication. `HasOrganizationPermission` attaches the membership to the request only after the checks succeed. Views still pass the URL id into the service. Body fields are not accepted as a substitute for that URL.

`GET /api/v1/organizations/` returns organizations where the caller has an active membership and the organization is active. It is not a directory of every tenant.

## Real-time connections

Channels is not installed. A later socket must authenticate the same user as the HTTP API, then authorize the organization on subscribe and again before delivering a tenant event. A connection that presents only an organization id is rejected. Fan-out rooms should be named with the organization id after that check, not before it.

## What this milestone does not do

It does not add row-level security policies in PostgreSQL, a schema per tenant, or a second database. Application checks plus the membership unique constraints are the isolation mechanism until a later review decides otherwise.
