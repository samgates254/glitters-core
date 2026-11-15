# GLITTERS Equity & Investment Solutions — readiness

Date: 2026-11-11. This note records what the working tree can do. It is not a launch approval, and nothing in it has been committed, pushed, or deployed.

## What was already present

- Marketplace property discovery, listing detail, and public listing enquiries (`POST /api/v1/marketplace/listings/{reference}/enquiries/`, response `id` and `status` only).
- Authenticated property publishing behind an existing mandate. The public site does not create a second property inventory.
- Organizations and `property.publish` for owner, admin, and manager. `leads.view` and `leads.manage` stay reserved.
- Staff API access requires a confirmed MFA session. Django admin does not enforce TOTP.
- Uncommitted marketplace enquiry work, including `marketplace/0003_enquiry_enquiryevent_and_more.py`. That migration is still not applied to the development database.

## What changed

- `apps.solutions` is registered in `INSTALLED_APPS` and included at `/api/v1/`.
- The public site brand is GLITTERS EQUITY & INVESTMENT SOLUTIONS. Header, footer, and homepage expose six desks: Properties, Auctions, Insurance, Projects, Investments, and Services.
- Editable SVG marks live in `glitters-web/public/brand/equity/`. The earlier mark at `public/brand/mark.svg` is unchanged.
- `/workspace` still has no site header or footer.

## What was added

- Auction listings, private and public documents, and auction enquiries. Publication is a staff action. Draft, pending verification, approved, withdrawn, and expired are not public. Approved is not the same as published.
- Insurance products with request-a-quote, and an empty adapter registry for a later authorised API. No premium is stored or shown unless that adapter returns one.
- Life enquiries accept only `individual`, `family`, or `partner_product`. Medical-history fields are rejected.
- Development projects, investment opportunities, and a 21-service directory. Providers are not described as licensed or certified unless `credentials_checked` is true.
- `CommercialEnquiry`, separate from the marketplace `Enquiry`, with staff export, partner isolation, follow-ups, and anonymisation after close.
- Pages for the six desks, privacy, terms, a staff enquiry desk, and a partner auction desk.

## What the tests show

Pytest on the isolated test database, after formatting:

`tests/unit/test_solutions_domain.py` and `tests/integration/test_solutions_workflows.py` — 14 passed.

Those tests cover auction publication, private documents, both insurance quotation modes, life-form rejection, project and investment publication, the service catalogue, partner isolation, export, anonymisation, and the staff MFA gate.

Also re-run and passed:

- `test_role_matrix_and_reserved_permissions`
- `test_enquiry_throttle_rates_are_explicit`
- `test_browser_enquiry_requires_a_csrf_token`

Ruff check passed for the solutions app and its tests. `tsc --noEmit` passed for `glitters-web`. ESLint passed on the pages and header edited in this pass.

## What the browser showed

Checked against `next dev` on `http://127.0.0.1:3001` while Core's solutions tables were absent:

- Primary navigation reaches Properties, Auctions, Insurance, Projects, Investments, and Services. The mobile menu lists the same six plus About, Contact, and Workspace.
- The homepage keeps Buy, Rent, Land, and Short stay, and adds the six desks.
- Auctions, insurance, projects, and investments say the live catalogue is not available. They do not show a sample price or a sample quotation.
- The services page lists all 21 catalogue names and says the live directory is not available. A service page does not open an enquiry form until the API record loads.
- The properties page still renders the existing discovery filters.
- The workspace page does not render the public header or the insurance disclaimer footer.
- The staff and partner buttons run. With nothing listening on `127.0.0.1:8010`, both report that the desk could not be loaded. They do not invent rows.
- Navigation does not overflow the page at 1440px or 1100px.

The process on port 3000 was `next start` of an older build. It does not contain this navigation until the app is built again. That build was not run, because it would replace the `.next` output that process is serving.

## What still needs an external partner

- An insurance premium, only after a licensed insurer or authorised intermediary supplies an adapter in `INSURANCE_QUOTE_ADAPTERS`. The registry ships empty.
- An investment price, only when a partner supplies `price_minor`, `data_source`, and `price_as_of`. There is no NSE scrape and no guaranteed return.
- Auctioneer licence text is a recorded statement. GLITTERS does not look it up in a regulator register.
- Bank, auctioneer, insurer, and investment-partner onboarding is an API and staff-approval workflow. There is no separate onboarding product, no bidding, no settlement, and no securities execution.

## Database

Created and not applied to development or production:

- `apps/solutions/migrations/0001_initial.py`
- `apps/solutions/migrations/0002_seed_service_categories.py`

Applying them with `migrate` would also apply the uncommitted marketplace enquiry migration `0003`. Do not do that without an explicit approval.

## Remaining compliance

- Do not describe GLITTERS as an insurer, insurance broker, investment adviser, stockbroker, or licensed auctioneer.
- Privacy and terms describe the product that is built. They are not a counsel-reviewed production policy.
- Life forms do not collect a medical history. Sensitive title files and private auction documents are not public.
- Django admin can edit these records and does not require TOTP. The staff API does.
- Partner operators are organization owner, admin, or manager through the existing `property.publish` permission. Members and viewers are refused. `leads.view` and `leads.manage` stay ungranted.

## Production deployment

1. Approve the database change, including the consequence that marketplace `0003` would be applied with the solutions migrations.
2. Keep private auction files behind the Django download views. Nginx currently proxies the API and does not alias `/media`.
3. Leave the insurance adapter registry empty until a written partner integration exists.
4. Require confirmed MFA for staff API use.
5. Rebuild and restart the Next.js app. Do not treat the generated SVG marks as a final brand-vendor master.
6. Have counsel review the privacy and terms pages before they are presented as the production legal text.
