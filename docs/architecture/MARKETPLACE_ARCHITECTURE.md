# Marketplace architecture

Status: implemented in M5. Decisions are in [ADR — Marketplace engine](decisions/ADR-marketplace-engine.md), [ADR — Listing authorization](decisions/ADR-listing-authorization.md), and [ADR — Marketplace location disclosure](decisions/ADR-marketplace-location-disclosure.md).

## Distinctions

| Concept | Record | Meaning |
|---|---|---|
| Property | `property` | The physical asset. Owned by `apps.properties`. |
| Listing | `listing` | One commercial offer for that property. |
| Listing mandate | `listing_mandate` | Authority to advertise. Not legal title. |
| Moderation | listing status plus staff fields | A platform decision about publication. Not a licence or a title check. |
| Transaction | none | Sale, lease, booking, invoice, and payment stay out of M5. |

A property may have no listing. It may have many historical listings. Listing status is not stored on the property. Property type is not listing type. An apartment can be listed for sale and, separately, for rent.

`apps.marketplace` may import properties, organizations, locations, and identity. Properties do not import marketplace. Locations do not import organizations or properties.

## Listing types

`sale`, `long_term_rental`, `short_term`, `commercial_lease`, `land_sale`, `development`.

Soft eligibility:

| Listing type | Property types |
|---|---|
| `sale` | any |
| `land_sale` | land |
| `commercial_lease` | commercial, industrial, mixed use |
| `long_term_rental`, `short_term` | residential, hospitality, mixed use |
| `development` | land, mixed use |

One open listing is allowed per property and listing type. Open means draft, pending review, published, paused, or rejected. Different types may be open together.

The public reference is `LST-` plus random hex. The property's `GLT-` reference and the property UUID are not on the public listing payload.

## What a published listing requires

Checked on submit, approve, and resume, and again by the public queryset:

- Title and description.
- Active property.
- Active mandate for that property, in force today, scoped to the listing type or `any`.
- At least one stored listing photograph.
- Active advertiser with a verified email.
- A current price whose billing period matches the type.
- When the representative is an organization: the organization is active, and the advertiser's membership is active and includes `property.publish`.
- An assisted description has been accepted.

Account or organization `active` is not legal verification. `registration_reference` and `phone_verified_at` are not consulted.

The publication term is 90 days. There is no Celery beat job. Overdue rows stay hidden from the public collection without a write. Public detail, and advertiser commands that hit an overdue published or paused row, persist `expired` and then return 404 or 409. Staff may batch-expire up to 500 rows.

## Commercial terms

`listing_price` is append-only for amounts. A change inserts a row and sets `superseded_at` on the previous current row. `amount_minor` is a non-negative integer. It is null only for `price_on_application`. Currencies are KES, UGX, TZS, RWF, and USD, stored as exact uppercase ISO 4217 codes. Presentation is `asking` or `price_on_application`. Billing periods are `sale`, `night`, `week`, `month`, and `year`. Price changes are allowed only while the listing is a draft. Price-range search skips price-on-application. A currency-only filter includes it. No deposit, commission, escrow, invoice, or payment row exists.

## Media

`property_media` stores a private object key, not image bytes. Cloudflare R2 is the first provider, behind `apps.marketplace.storage`. Development and tests use an in-memory adapter and do not need R2 credentials. Staging and production default to R2 and fail only when a request needs an unconfigured bucket. The server generates `properties/{property_uuid}/media/{media_uuid}/original`, returns a short-lived presigned PUT, and does not store that URL. `stored` means the object was found, the size matched, and the bytes decoded as JPEG, PNG, WebP, or PDF. A client `upload_status` or storage URL cannot mark a file stored. Photographs use visibility `listing`. PDF documents use visibility `private`. A listing attaches verified photographs of the same property, and only while the listing is a draft. Documents cannot be attached. An active in-force mandate lets the representative upload, list, and download photographs when they would otherwise receive 404. Private documents still require property update rights. Public listing media exposes `/api/v1/marketplace/listings/{reference}/media/{link_id}/`, not the object key. That GET mints a short-lived signed URL for one verified listing photograph. Removal rejects the row, detaches listing links, and deletes the object. It does not by itself change the listing status. Session expiry and leftover objects are cleaned when a new upload starts. There is no media Celery task.

## Discovery

Public search uses PostgreSQL. Filters: listing type, property type, country, administrative area and its descendants, locality, price range, currency, bedrooms, bathrooms, floor area, land area (`plot_area_sqm`), publication window, and `q` (2 to 80 characters) with `SearchQuery` config `simple`. Sorts are `published_at` and `price`, plus an id tie-break. Default is `-published_at`. Geospatial parameters are rejected. Indexes cover status and publication time, listing type and status, property and status, advertiser and status, advertising organization and status, and a GIN index on the search vector. Queries use `select_related` and `prefetch_related` and defer the geography column. There is no Elasticsearch, OpenSearch, or vector index.

## AI boundary

`description_origin` is `human` or `ai_assisted`. Assisted text stores `ai_approved_at` only after an authorized person accepts it on a draft. Editing the text clears that timestamp. Submit refuses unaccepted assisted text. Nothing in M5 calls a model, stores embeddings, or publishes without the human workflow. A later assistant must call the same property and organization services.

## Audit

`listing_event` is append-only. It records actor, listing when there is one, property, organization id when there is one, action, timestamp, request id, and metadata. Metadata drops tokens, secrets, coordinates, evidence references, storage references, object keys, and presigned URLs. Rejection reasons stay on the listing for the advertiser and for moderators. They are not public. Organization events are not duplicated. Upload start, verification, rejection, download, and removal are recorded. A public signed photograph URL is not.

## API

Routes are in [V1.md](../api/V1.md). Lifecycle detail is in [LISTING_LIFECYCLE.md](LISTING_LIFECYCLE.md). Mandates are in [LISTING_MANDATES.md](LISTING_MANDATES.md). Public location rules are in [MARKETPLACE_LOCATION_PRIVACY.md](MARKETPLACE_LOCATION_PRIVACY.md).
