# ADR — Marketplace engine

- Status: accepted
- Date: 2026-11-11
- Milestone: M5
- Builds on: [ADR — Property domain](ADR-property-domain.md), [ADR — Organization and RBAC](ADR-organization-and-rbac.md)

This decision is accepted before the M5 marketplace migration.

## Context

M4 records a physical property. It does not advertise that property. A later sale, lease, booking, invoice, or payment is also a different fact from the advertisement itself.

The same apartment can be offered for sale and, at another time, for rent. The property type stays an apartment. The listing type changes. Storing a listing status on the property would collapse those offers and would make a draft listing look like a change to the asset.

Publication is a platform decision. It is not a title search, a business-registration check, or an agent-licence check. GLITTERS does not integrate with a land registry in this milestone.

Discovery has to stay on PostgreSQL and PostGIS. A second search cluster, embeddings, and a recommendation service are not required to filter published listings.

## Decision

`apps.marketplace` may import properties, organizations, locations, and identity. Those apps do not import marketplace. There is no second user model and no second permission registry.

### Separate records

- A property remains the M4 asset. Marketplace code does not copy its address, coordinates, or party rows onto the listing.
- A listing is one commercial offer for one property. It has its own status, promotional text, price rows, and selected media.
- A listing mandate is the authority to advertise. It is specified in [ADR — Listing authorization](ADR-listing-authorization.md).
- Moderation is a staff decision about publication. A reviewed listing does not become a verified title.
- Settlement, deposits, commission, escrow, invoices, M-Pesa, and booking payments are out of scope. No tables are added for them.

### Listing types

The closed set is `sale`, `long_term_rental`, `short_term`, `commercial_lease`, `land_sale`, and `development`. Adding a type is a marketplace change. It does not restructure the property catalogue.

Soft eligibility checks the property type before a draft is stored:

- `land_sale` requires land.
- `commercial_lease` requires commercial, industrial, or mixed-use.
- `short_term` and `long_term_rental` require residential, hospitality, or mixed-use.
- `development` allows land and mixed-use.
- `sale` allows any property type.

One open listing is allowed per property and listing type. Open means draft, pending review, published, paused, or rejected. Withdrawn, expired, and completed rows do not block a later offer. Two different types may be open together.

### Lifecycle

Advertiser transitions:

- draft → pending review, or withdrawn
- pending review → withdrawn
- published → paused, withdrawn, or completed
- paused → published, withdrawn, or completed
- rejected → draft, or withdrawn

Staff transitions:

- pending review → published, or rejected
- published → paused, as a moderation hold
- paused with a hold → pending review, which must be approved again

System transitions:

- published or paused → expired, when `expires_at` has passed
- published, paused, or pending review → withdrawn, when the mandate is revoked or expires

Terminal withdrawn, expired, and completed rows are not reopened. Renewing an expired listing inserts a new draft with a new `LST-` reference and copies title, description, highlights, the current price, and media links. The expired row stays expired. The recorded advertiser on the new row is the person who renews it.

An advertiser cannot publish. Approval is staff-only, and staff must have confirmed MFA. `is_staff` is not an organization role and does not skip the mandate.

A published listing needs a title, a description, an active property, an effective mandate whose scope is the listing type or `any`, at least one stored listing photograph, an eligible advertiser, and a price whose billing period matches the type. An assisted description also needs an acceptance timestamp. The publication term is 90 days.

If a published or paused listing is already past `expires_at`, a later advertiser pause, resume, withdraw, or complete expires it and returns 409. Renew expires it and then creates the new draft. The public detail request expires it and returns 404. The public collection does not write; it hides overdue rows until a later expiry write.

### Commercial terms

Each price is a new `listing_price` row. The previous current row receives `superseded_at`. Amounts are integers in minor units (`amount_minor`). There is no float column. Null amount is allowed only for `price_on_application`. Currencies are KES, UGX, TZS, RWF, and USD. Sale, land sale, and development use `sale`. Long-term rental and commercial lease use `month` or `year`. Short-term uses `night` or `week`. Price edits are allowed only while the listing is a draft.

### Media

M5 accepted a client storage reference and treated `stored` as acceptance of that reference, without fetching bytes. Photographs were JPEG, PNG, or WebP with visibility `listing`. Private documents were PDF with visibility `private`. Documents could not be attached to a listing. M6-A replaces this paragraph. See [ADR — Media storage](ADR-media-storage.md).

### Discovery

Public search reads eligible published listings. It filters listing type, property type, country, administrative area (the area and its descendants, not `ST_Contains`), locality, price range, currency, bedrooms, bathrooms, floor area, land area, publication window, and a short text query. Text uses PostgreSQL `simple` full-text search on title, description, and highlights. Sorts are publication time and asking amount, with a stable id tie-break. Price-range filters skip price-on-application. There is no Elasticsearch, OpenSearch, or vector index.

### Assisted text

`description_origin` is `human` or `ai_assisted`. Human text cannot carry `ai_approved_at`. Editing assisted text clears that timestamp. Submission requires it when the origin is assisted. No model is called, and nothing is published by an automated writer.

### Audit

`listing_event` is append-only. It records the actor, listing when there is one, property, organization id when there is one, action, request id, and metadata. Metadata drops tokens, secrets, coordinates, evidence references, and storage references. Organization events are not duplicated for listing actions.

## Consequences

- A property can exist with no listing, and it can have many historical listings.
- Public clients never receive the internal `GLT-` reference or the property UUID from the marketplace API.
- Expiry can lag until the next detail read, publisher mutation, or staff `expire-due` batch. There is no Celery beat task.
- Changing a parent access grant still does not copy new grants onto existing children. That remains an M4 creation-time copy, with the revocation behaviour in the property ADR update.
- Operators must not describe a published listing as title-verified.

## Not chosen

- Listing status on `property`.
- One listing row reused after expiry.
- Self-service publication without staff review.
- Float or decimal money columns.
- Storing image bytes in PostgreSQL.
- A separate search service in M5.
