# Ecosystem requirements traceability

Status: audit on 2026-11-11. This is the map from the executive presentation, the digital ecosystem proposal, and the later auction, insurance, investment, projects, services, and rebrand instructions onto the code that exists.

It is not a claim that those products are built. A row is `Implemented` only when the current code and its tests cover that behaviour. `Partially Implemented` means a real subset exists and the rest of the proposal wording does not. `Planned` means the architecture can hold it and no module exists yet. `Blocked by External Dependency` means the work also needs a partner, credential, licence, or legal text that is not in either repository.

Nothing in this file is `Accepted`. There has been no client UAT.

## Repositories inspected

| Repository | Branch | Tip | Working tree |
|---|---|---|---|
| `~/Projects/glitters-core` | `feature/media-infrastructure` | `7f61a98` media infrastructure | Uncommitted M6-B.1 enquiry persistence and M6-B.2 public enquiry creation. `marketplace.0003` is not applied to the development database. |
| `~/Projects/glitters-web` | `main` | `d5ac81d` Create Next App | Uncommitted design-system shell, catalogue, and a Cesium workspace prototype. No API client. |

Last verified Core suite, from the M6-B.2 session on this date: 235 tests passed, Django check clean, migration check clean, Ruff clean. That run was not repeated for this audit. Web has a component catalogue. It is not a tested product against the API.

## What already exists

Core is one Django 5.2 monolith. Apps: `core`, `identity`, `organizations`, `locations`, `properties`, `marketplace`. PostgreSQL/PostGIS, Redis, and Celery are configured. Celery has no application tasks. Django Channels, payments, SMS, WhatsApp, Google Maps, insurer APIs, and market-data feeds are not installed.

The public API is `/api/v1/`. Three experiences are the target in `docs/architecture/SYSTEM_ARCHITECTURE.md`. They do not each own a database. Web does not call the API yet. The catalogue says authentication waits for a same-site proxy because the session cookie is `SameSite=Lax`.

Identity is email and password, email verification, session and opaque mobile tokens, TOTP for staff API access, emailed phone codes, and append-only identity events. Organizations have memberships, invitations, and RBAC. Organization types are agency, developer, property manager, landlord, hospitality, professional service, and contractor. There is no bank, auctioneer, insurer, or investment-bank type.

Properties are canonical records with parties, grants, attributes, and PostGIS coordinates. Public listings disclose country, administrative area, and locality only. Exact coordinates stay privileged. A party is not title. A mandate is not title.

Marketplace listings cover sale, long-term rental, short-term, commercial lease, land sale, and development. Lifecycle is draft, pending review, published, rejected, paused, withdrawn, expired, and completed. Staff with confirmed MFA approve publication. Mandates, integer minor-unit prices, verified private photographs, and public discovery filters exist. Public enquiry creation exists in the working tree and stores `new` plus `enquiry.created`. There is no advertiser inbox. `leads.view` and `leads.manage` are reserved and granted to nobody.

Web routes are `/`, `/catalogue`, and `/workspace`. Navigation is the catalogue and the workspace, not the eleven public divisions. The homepage names the company GLITTERS Equity Investment Solutions and uses a brass accent. It does not ship the production logo system, and the number ticker is a design demonstration, not a database KPI.

## Status key

| Status | Meaning |
|---|---|
| Implemented | Present in Core or Web and covered by the behaviour that milestone tested. |
| Partially Implemented | A narrower version exists. |
| Planned | Not built. The monolith can add it later. |
| Blocked by External Dependency | Also waiting on a partner, credential, or approved legal text. |

Frontend status is separate. Unless a row says otherwise, the Web column is `Planned`.

## Part 1 — Participants

| Capability | Backend | Frontend | Notes |
|---|---|---|---|
| One account model for people | Implemented | Planned | `identity.User`. Do not add a second user model. |
| Email registration, verification, session, mobile token, password reset, MFA for staff API | Implemented | Planned | Phone codes are emailed, not SMS. Django admin does not require TOTP. |
| Organization, membership, invitation, role | Implemented | Planned | Roles: owner, admin, manager, member, viewer. |
| Agency, developer, landlord business, property manager, hospitality operator, professional service, contractor | Partially Implemented | Planned | Types exist. Portals for those types do not. |
| Buyer, seller, tenant, guest, investor as account roles | Planned | Planned | A person is a user. These are not roles on `User`. |
| Banks, auctioneers, insurers, investment banks | Planned | Planned | No organization type and no partner workflow. |
| Human review before a badge means professional registration | Partially Implemented | Planned | Listing moderation and email verification exist. Licence review does not. Badges must not imply title. |

## Part 2 — Three experiences

| Experience | Backend | Frontend |
|---|---|---|
| Public website | Partially Implemented. Public listing search, detail, photo URL, and enquiry create. | Planned. Homepage is a brand shell. |
| Role workspaces | Partially Implemented. Publisher listing and mandate APIs, organization APIs, property APIs. | Planned. |
| Control Center | Partially Implemented. Staff moderation of listings requires MFA. Django admin is a bootstrap, not the Control Center. | Planned. |

## Part 3 — Public navigation and pages

| Item | Status |
|---|---|
| Home, Properties, Auction, Projects, Insurance, Investments, Professional Services, Short Stays, Property Management, About, Contact | Planned |
| Calls to action from Buy through Become a Partner | Planned |
| About, Contact, Services, Agents, Developers, BnB, Land, Property Management, List, Become an Agent, Become a Provider, Help, FAQ, Privacy, Terms | Planned |
| Auction, Insurance, Investments, Projects, Institutional Partners pages | Planned |
| CMS for public copy without a deploy | Planned |

Do not add these as static pages that pretend the workflows exist.

## Part 4 — Discovery

| Capability | Backend | Frontend |
|---|---|---|
| Listing types for sale, rent, commercial, land, short-term, development | Implemented | Planned |
| Filters for type, country, area, locality, price, beds, baths, areas, dates, text, sort | Implemented | Planned |
| Public grid from the API | Implemented as JSON pagination | Planned |
| Intent picker, natural language, amenities, furnished, verified badge, map, list toggle, saved search, alerts, favourites, comparison, share | Planned | Planned |
| AI interpretation of a sentence into filters | Planned | Planned. Suggestions stay reviewable. AI must not decide ownership or money. |
| Auction and project catalogues | Planned | Planned |

## Part 5 — Listings and agent operations

| Capability | Backend | Frontend |
|---|---|---|
| Draft listing, mandate, price, submit, staff approve or reject, pause, resume, withdraw, complete, renew, expire | Implemented | Planned |
| Photograph upload, server-side verification, private storage, public photo for an eligible listing | Implemented | Planned |
| Documents kept private and refused as public listing media | Implemented | Planned |
| Advertiser cannot self-publish | Implemented | Planned |
| 10-step agent onboarding and credential review | Planned | Planned |
| 20-step wizard, video, virtual tour, amenities, duplicate detection, perceptual hash | Planned | Planned |
| Verified Agent, Verified Owner, Document-Reviewed badges | Planned | Planned |
| Agent dashboard and its 15 indicators | Planned | Planned |
| Agency team management beyond membership roles | Partially Implemented | Planned. Memberships exist. A brokerage CRM does not. |

The implemented lifecycle names differ from the proposal's Submitted → Under Review → Approved → Published → Unpublished. The code uses `pending_review`, `published`, `rejected`, `paused`, `withdrawn`, `expired`, and `completed`. Keep those names.

## Part 6 — Customer account

| Capability | Status |
|---|---|
| Register and manage a profile | Implemented in the identity API. |
| Browse listings without an account | Implemented. |
| Public enquiry without an account | Implemented in the working tree. CSRF is required. |
| Saved search, favourites, comparison, messages, viewings, quotes, auction interest, insurance records, investment records, notification and privacy preferences | Planned. |

## Part 7 — CRM and viewings

| Capability | Status |
|---|---|
| Enquiry statuses `new`, `contacted`, `qualified`, `unqualified`, `converted`, `spam`, `closed` | Partially Implemented. The graph is in domain code. No transition API. |
| Public create sets `new` and writes `enquiry.created` | Implemented in the working tree. Migration `0003` is not applied. |
| Assignment, SLA, notes, source, budget, negotiation, lost reason | Planned. `leads.view` and `leads.manage` stay reserved. |
| Viewing statuses Requested, Confirmed, Completed, Cancelled, Missed | Planned. |

The proposal's Viewing Scheduled and Negotiation states are not the enquiry graph. Add viewings as their own records. Do not overload enquiry status.

## Part 8 — Auctions

| Capability | Status |
|---|---|
| Auction discovery, partner profiles, distressed-sale listings, registration of interest | Planned |
| Online bidding, deposit custody, settlement | Out of scope until a separate legal approval. Do not build them as part of discovery. |
| Confidential bank documents | The private-media pattern can hold them later. No auction model exists. |

## Part 9 — Projects

| Capability | Status |
|---|---|
| `development` listing type and `developer` organization type | Partially Implemented. A development listing is still one marketplace listing, not a project with units, towers, or milestones. |
| Project statuses, brochures, progress, off-plan inventory, installment schedules | Planned |

## Part 10 — Insurance

| Capability | Status |
|---|---|
| General and life product catalogue, quote request, consent, partner routing | Planned |
| Automated premium API | Blocked by External Dependency |
| Invented premiums or public medical-history forms | Must not be built |

## Part 11 — Investments and NSE

| Capability | Status |
|---|---|
| Partner-supplied opportunity pages and quote requests | Planned |
| Live NSE prices, trade execution, custody, personalized recommendations | Blocked by External Dependency. Do not invent prices or returns. |

## Part 12 — Professional services

| Capability | Status |
|---|---|
| Organization type `professional_service` and `contractor` | Partially Implemented |
| Categories A–E and the named services | Planned as a future taxonomy. They are not rows in the database. |
| Provider portal, quotes, appointments, chat, reviews, ten-step delivery | Planned |
| Claiming a provider is licensed before review | Must not be built |

## Parts 13–15 — Landlord, tenant, short stay

| Capability | Status |
|---|---|
| Landlord organization type, property portfolio, grants, archive | Partially Implemented. No units, leases, rent ledger, arrears, expenses, or exports. |
| Tenant invitation, lease, balance, M-Pesa, receipts, maintenance, utilities | Planned. M-Pesa is Blocked by External Dependency. |
| Short-term listing type and public eligibility for nightly or weekly prices | Partially Implemented. No calendar, booking, guest, review, or payout. |
| Double-booking prevention | Planned. Do not fake a booking that does not lock a stay. |

Financial figures in those portals must come from ledger rows. There is no ledger.

## Part 16 — Maps

| Capability | Backend | Frontend |
|---|---|---|
| PostGIS storage, country and area seed, address resolve, nearby and bounding-box search on accessible properties | Implemented | Planned |
| Public listing map, clusters, polygons, commute, Places, directions, demand heatmaps | Planned | `/workspace` loads Cesium. It is not a listing map and has no property feed. |
| Exact gate coordinates on a public page | Refused by the listing serializer | No public pin |

Google Maps Platform is not integrated. The proposal names it. The web prototype uses Cesium and a dotted Kenya map. Align that choice before adding a second map vendor.

## Part 17 — Communication

| Capability | Status |
|---|---|
| Enquiry event and listing, property, organization, and identity audit events | Partially Implemented |
| Channels, WebSockets, five chat threads, email, SMS, WhatsApp | Planned. ADR 0008 deferred Channels. Email backend is console in development. |
| Eight proposal triggers plus auction, insurance, investment, project, and service events | Planned |

## Part 18 — Control Center

All 14 modules are `Planned` as a product, with these exceptions already in the API:

| Module slice | Status |
|---|---|
| Staff listing approve, reject, suspend, reinstate, expire-due | Implemented. MFA required. |
| Organization status changes by staff or owner deactivation | Implemented |
| Account suspend and reactivate through identity status rules | Partially Implemented |
| User search, agent applications, viewings, chat desk, subscriptions, payments, BnB, services, CMS, BI, audit console, system configuration UI | Planned |
| Append-only domain events | Implemented for the modules that write them. No admin screen can delete them. Django admin is not that guarantee by itself. |

## Part 19 — 24 KPIs

`Planned`. Some counts could be queried from users, listings, and enquiries. They are not exposed, and the homepage number ticker is not a KPI. Do not fill a dashboard with sample numbers.

## Part 20 — Business intelligence

`Planned`. There is no page-view, search-log, or campaign table. Enquiry and listing events are not a funnel warehouse.

## Part 21 — Automation and AI

| Item | Status |
|---|---|
| Description origin can record `ai_assisted`, and publication requires a human acceptance timestamp | Partially Implemented. No model is called. |
| Assignment, reminders, rent reminders, payment confirmation, duplicate alerts | Planned |
| Recommendations, matching, natural-language search, lead scoring, support assistant, demand analysis | Planned |

## Part 22 — Monetisation

`Planned`. Prices and commission rates must be configuration, not constants. No plan, entitlement, or invoice model exists. Public browsing and the public enquiry do not charge the customer.

## Part 23 — Target stack

| Layer | Audit |
|---|---|
| Django, DRF, `/api/v1/`, PostgreSQL, PostGIS, Redis, Celery configuration | Present |
| Next.js, React, TypeScript, Tailwind | Present in Web as a prototype. App Router 16. |
| Channels, WebSockets, Cloudinary or S3, CDN, Google Maps, Daraja, SMS, WhatsApp, insurer API, market data | Not present. Media uses a private S3-compatible adapter and an in-memory store in tests. Production bucket credentials are not wired. |

## Part 24 — Security and privacy

| Control | Status |
|---|---|
| Password hashing, email verification, session and token rotation, staff MFA, organization RBAC, property grants, CSRF on unsafe cookie routes, throttles, private media, append-only events | Implemented for the modules that exist |
| Public enquiry throttle by address and by address plus email digest | Implemented in the working tree |
| XSS, rate limits, and secret handling in deployed settings | Partially Implemented. Staging and production refuse debug and placeholder secrets. |
| Cookie banner, privacy and terms pages, access, correction, and deletion workflows | Planned |
| Payment callback verification | Planned with payments |
| Penetration test, vulnerability scan in CI, backup drills | Not evidenced here. `pip-audit` was clean at earlier milestones. This audit did not re-run it. |

## Part 25 — Payments

`Planned` and `Blocked by External Dependency` for live Daraja. No card data model. No sandbox payment app. ADR 0008 still applies.

## Part 26 — Brand

| Item | Status |
|---|---|
| Company name on the web homepage | Partially Implemented |
| Navy, gold, magenta, white token system and the ten logo assets as production SVG | Planned. `public/brand/` holds source drawings. Do not use a poster as the site logo. |
| Control Center dark theme | Planned |

## Part 27 — Design system

Web has a component catalogue and design notes under `glitters-web/docs/`. The ten product screens and the auction, insurance, investment, and project screens are `Planned`. Catalogue demos are not those screens.

## Part 28 — Twelve horizons

`Planned`. None of the twelve expansion products are in the schema. Currencies currently allowed on asking prices are KES, UGX, TZS, RWF, and USD, stored as integer minor units.

## Part 29 — Nine phases

| Phase | Audit |
|---|---|
| 1 Discovery | This document. The older architecture set in `docs/architecture/` is the current blueprint for identity, organizations, locations, properties, and marketplace. It does not yet specify auction, insurance, or investment schemas. |
| 2 Brand and UI | Started as an uncommitted web shell. Not approved as the product UI. |
| 3 Core marketplace | Largely implemented in Core through M6-A and the uncommitted enquiry create. Web discovery is not built. Maps in the proposal sense are not built. |
| 4 Control Center | Not built, apart from listing moderation APIs. |
| 5 Landlord and tenant | Not built. |
| 6 BnB bookings | Not built. Short-term listing type exists. |
| 7 Services | Not built. Organization types exist. |
| 8 Integrations | Not built. |
| 9 Launch | Not started. |

## Part 30 — Deliverables

| Deliverable | Status |
|---|---|
| Secure authentication | Implemented in Core. |
| Listing and private media management | Implemented in Core. |
| Public website, marketplace UI, buyer workspace, agent portal | Planned |
| Landlord, tenant, BnB, service provider, Control Center | Planned |
| Maps | Partially Implemented in Core. Web map is a prototype. |
| Chat, viewings, subscriptions, payments, BI, CMS, notifications | Planned |
| Audit logs | Partially Implemented as append-only events. |
| Technical documentation | Partially Implemented for M1–M6. |
| Tests | Core suite last counted 235 passed. Web product tests are not the acceptance suite. |
| Production deployment and handover | Not started. |
| Auction, insurance, investment, projects, expanded services, partner management | Planned |

## Part 31 — Client inputs

All 21 proposal inputs, plus bank, auctioneer, insurer, investment-bank, NSE data, developer, and service-credential inputs, are outstanding. This repository does not contain official registration details, vector logo approval, DNS, Daraja credentials, support numbers, or signed privacy copy. Do not invent them.

## Rules that later work must keep

- One user model: `identity.User`.
- One database. Three experiences call `/api/v1/`.
- Money is integer minor units.
- Public pages do not receive exact coordinates.
- A mandate, a party, a registration reference, and a badge are not legal title.
- `listing.manage`, `leads.view`, and `leads.manage` stay reserved until the milestone that defines them.
- Do not apply `marketplace.0003` until asked.
- Do not add bidding, premium quotes, NSE prices, or payment capture ahead of the partner and the legal approval.
- Web must not mark a screen complete when the API for that workflow does not exist.
