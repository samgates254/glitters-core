# Domain model

Status: identity accounts, organization membership, locations, canonical properties, and marketplace listings exist. Payment tables do not.

## Implemented now

### Identity

`identity.User` is the person who can authenticate. The design is in [IDENTITY_ARCHITECTURE.md](IDENTITY_ARCHITECTURE.md).

- Primary key is a UUID.
- Login identifier is a normalized, unique email address. The whole address is stored lowercase.
- Phone is optional, stored as E.164, and unique only after verification. An unverified phone is not identity.
- Password storage uses Django's hasher pipeline, with Argon2 first.
- `status` is the account lifecycle. `is_active` follows that status.
- Staff and superuser flags exist for the Django admin bootstrap and for staff-only schema access. They are not organization roles.
- The record does not belong to an organization.
- The record has no ownership column. A property may name the user as `created_by` or as a party.
- There is no account-type column.

Sessions, mobile credentials, one-time tokens, TOTP devices, recovery codes, and identity events are separate tables. They reference `settings.AUTH_USER_MODEL`.

Organization membership, invitations, and organization audit events are separate tables in `apps.organizations`. The user record still has no organization role. A membership grants nothing unless the user status is `active`, the membership is `active`, and the organization is `active`. Creating a listing will never imply ownership. The model is specified in [ORGANIZATION_ARCHITECTURE.md](ORGANIZATION_ARCHITECTURE.md).

### Locations and properties

`apps.locations` holds countries, administrative ladders, localities, and addresses. `apps.properties` holds the canonical asset, its characteristics, its location point, claimed parties, access grants, and property audit. The designs are in [LOCATION_ARCHITECTURE.md](LOCATION_ARCHITECTURE.md) and [PROPERTY_ARCHITECTURE.md](PROPERTY_ARCHITECTURE.md).

A property is not a listing. A party role is not legal title. Management context is not ownership. An organization party is not an access grant. Property status is `draft`, `active`, or `archived`.

### Marketplace

`apps.marketplace` holds listings, listing mandates, asking prices, property media references, listing media selections, and listing audit. The design is in [MARKETPLACE_ARCHITECTURE.md](MARKETPLACE_ARCHITECTURE.md). Listing status is not a property column. A mandate is not a party claim and is not legal title. Moderation is not title, business-registration, or licence verification.

## Future Django apps

`organizations`, `locations`, and `properties` now exist. The other apps are not created. Creating an empty app for every noun would hide the real boundaries.

| App | Owns | Depends on |
|---|---|---|
| `identity` | Users, credentials, sessions, MFA, security events | nothing domain-specific |
| `organizations` | Agencies, managers, developers, service firms, memberships, invitations, org roles | identity |
| `locations` | Countries, administrative areas, localities, addresses. Implemented. Boundary polygons are not stored. | nothing domain-specific |
| `properties` | Properties, hierarchy, characteristics, parties, access. Implemented. Listing lifecycle stays out of this app. | organizations, locations |
| `verification` | Agent, agency, owner, and provider review, fraud reports. Not created. Listing moderation lives in marketplace and is not title review. | identity, organizations, properties |
| `marketplace` | Listings, mandates, asking prices, media references, public discovery. Implemented. Saved searches and enquiries are not. | properties, organizations, locations, identity |
| `agents` | CRM, assignment, viewings, follow-ups | organizations, marketplace |
| `rentals` | Portfolios, leases, rent schedules, charges, receipts, arrears, maintenance | properties, organizations, identity |
| `hospitality` | BnB inventory, calendars, prices, reservations, stays, reviews | properties, identity |
| `services` | Provider profiles, quotations, appointments | organizations, verification |
| `billing` | Plans, entitlements, invoices | organizations |
| `payments` | Attempts, confirmed transactions, allocations, refunds, reconciliation | billing |
| `communications` | Conversations, notifications, email and SMS adapters | identity |
| `administration` | Control Center policies, audit review, configuration | explicit service calls, not cross-imports of every model |

Marketplace media stores a private object key, not file bytes. JPEG, PNG, WebP, and PDF are accepted only after server-side verification. Private PDF documents are not served on public listing payloads. The storage adapter is provider-neutral. Cloudflare R2 is the first provider. See [ADR — Media storage](decisions/ADR-media-storage.md).

```text
identity --------------------------+
    |                               |
    v                               v
organizations                    locations
    |                               |
    +--------------> properties <---+
                        |
        +---------------+----------------+
        |               |                |
        v               v                v
   marketplace       rentals        hospitality
        |               |                |
        v               v                v
     agents          billing --------> payments
                        |
                        v
                 communications
                        |
                        v
                 administration
```

`verification` and `services` sit beside organizations and properties. They do not own the property row.

## Dependency rules

- Identity does not import other domain apps.
- A domain reads another domain through a service or selector, not by reaching into private tables from a view.
- Circular app imports are a defect.
- Administration does not bypass domain services to change financial history.
- PostgreSQL constraints enforce the invariants that cannot be left to application code.

## Ownership and authority

These are different facts:

| Fact | Meaning |
|---|---|
| User | A person with credentials |
| Organization membership | The person acts inside an organization, with a role |
| Property ownership | A `property_party` claim. `verified` is reserved for a future authorized process. |
| Management authority | `management_context` and `property_access`. The managing organization is not the owner. |
| Listing mandate | `listing_mandate`. Authority to advertise one property inside a scope. A representative party is only a claim. |

A person may belong to several organizations. An agency may have many agents. A landlord may own several properties. A manager may manage properties for more than one owner.

## Money

Asking prices are integers in minor units on `listing_price.amount_minor`, with an ISO 4217 currency code. One shilling is 100 minor units. Floating-point columns are not used for money. M5 currencies are KES, UGX, TZS, RWF, and USD.

Payment initiation, a confirmed transaction, an allocation, a refund, a settlement, and a reconciliation entry are different records. A successful request to start a payment is not evidence that money arrived.

No payment tables exist yet.

## Booking integrity

Reservation and viewing conflicts will be prevented with database constraints and transactions, not with a cache lock as the only control. No booking table exists yet.

## Audit

`identity_event` records authentication events. It stores no passwords and no raw tokens. `organization_event`, `property_event`, and `listing_event` are append-only. Property and listing audit drop exact coordinates. Listing audit also drops evidence labels and storage references. The Django admin can list those rows and cannot add, change, or delete them. Financial audit is still a later table.

## Deletion

Soft deletion is not a global policy. It will be used only where a business record must remain visible after withdrawal. Financial and audit history is append-only rather than soft-deleted.

## Time

`USE_TZ` is on. The application zone is `Africa/Nairobi`. Stored timestamps are timezone-aware.

## Regulatory note

Kenya's Data Protection Act applies to identity documents, leases, and financial information once those records exist. This repository does not claim a compliance certification, a registration status, or a completed legal review. Access control, purpose limitation, retention, and breach handling have to be designed before those records are stored.
