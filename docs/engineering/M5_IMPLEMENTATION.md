# M5 implementation

Date: 2026-11-11. Branch: `feature/marketplace-engine`. Implementation commit: `553ed398d88c53edac92de637b84abd9dd1c3b23`. Parent: `ce3ff35e122ecc03aaf0077e577623a84eeaccce`.

## What shipped

`apps.marketplace` adds listings, listing mandates, asking prices, media references, public discovery, and moderation on the M4 property records. `identity.User` is unchanged. No package was added. Celery still has no application tasks. There is no payment, booking, deposit, commission, invoice, or escrow behaviour.

M5 also closes two carried security gaps:

- An old organization invitation cannot restore a removed or suspended membership. A fresh invitation returns that membership to `invited`. Acceptance is a separate step. Suspend and remove revoke pending tokens.
- Personal child grants record `origin` and `inherited_from`. Revoking a parent grant revokes inherited copies. Direct grants on descendants stay.

`property.publish` is granted to owner, admin, and manager. `listing.manage` stays reserved.

The architecture is in:

- `docs/architecture/MARKETPLACE_ARCHITECTURE.md`
- `docs/architecture/LISTING_LIFECYCLE.md`
- `docs/architecture/LISTING_MANDATES.md`
- `docs/architecture/MARKETPLACE_LOCATION_PRIVACY.md`
- `docs/architecture/decisions/ADR-marketplace-engine.md`
- `docs/architecture/decisions/ADR-listing-authorization.md`
- `docs/architecture/decisions/ADR-marketplace-location-disclosure.md`

## Schema

`properties.0002_access_provenance` adds `property_access.origin` and `property_access.inherited_from`. It does not edit `properties.0001_initial`.

`marketplace.0001_marketplace_foundations` creates `listing_mandate`, `listing`, `listing_price`, `property_media`, `listing_media`, and `listing_event`. It depends on `properties.0002_access_provenance`. Amounts are integers. The geography column is not copied onto the listing.

## API

Routes are under `/api/v1/marketplace/` and are listed in `docs/api/V1.md`. OpenAPI includes them. Publisher and moderation writes opt back into CSRF. Errors stay `{errors, request_id}`. Public reads return only eligible published listings.

## Verification

Commands were run from `/home/mr-robot/Projects/glitters-core` with the project virtualenv on 2026-11-11:

- `ruff check .` passed.
- `ruff format --check .` passed. 198 files were already formatted.
- `python manage.py makemigrations --check --dry-run` reported no changes.
- `python manage.py check` reported no issues.
- `pytest --cov=apps --cov=config --cov-report=term-missing` reported **152 passed** in 52.26 seconds. Coverage of `apps` and `config` was **89%** (6901 statements, 766 missed).
- `uv run pip-audit --progress-spinner off` reported `No known vulnerabilities found`.

The M4 suite was 127 passed at 91% coverage, at implementation commit `f088edc18667beb45b7482ba5aa2d1a0b9441016`. Those tests still run. Intentional updates assert that `property.publish` is granted to owner, admin, and manager, and that `listing.manage` stays reserved. They do not weaken identity, organization, or property security tests.

M5 tests cover draft creation and editing, submission, approval, rejection, resubmission, pause, resume, withdrawal, completion, expiration, invalid transitions, price rules and history, one open listing per type, media validation, public filters, pagination, sorting, personal and organization advertisers, agent representation, expired and revoked mandates, wrong property and wrong scope, inactive organizations, suspended and removed memberships, unverified email, invitation reactivation, grant provenance, and public coordinate privacy.

This workstation did not push the branch and did not run GitHub Actions.

## Limitations carried forward

- TOTP secrets are signed with salt `glitters.mfa.v1`, not encrypted at rest.
- Django admin does not require TOTP. Marketplace admin is inspect-only. It is not a second authorization path.
- Phone codes are emailed. `phone_verified_at` is not handset proof and is not read by listing authorization.
- `SameSite=Lax` still needs a same-site proxy. A browser on another site, such as a Next.js origin calling the API on another port, is unsupported.
- Failed logins are throttled and do not set `security_restricted`.
- Self-service organizations start `active`. A registration reference is not legal verification. Listing publication does not treat account or organization status as legal verification.
- Tenant isolation is application-level. There is no PostgreSQL row-level security policy.
- Invitation expiry is lazy.
- Members and viewers can read all of an organization's properties. `has_coordinates` and 100 metre distance bands remain on the property API.
- A suspended member receives 403 on a property they previously could see, which confirms that the id exists.
- An organization may manage a property addressed in another country.
- Management context is immutable. Archive is terminal. Active does not return to draft.

## Limitations introduced or left open by M5

- No deposit, rent collection, commission, escrow, invoice, payment reconciliation, M-Pesa, or booking payment.
- No process verifies legal title, business registration, or an agent licence. A self-declared mandate is labeled as a self-declaration.
- Cloudinary and S3 are not wired. Media rows store an accepted reference. The service does not fetch the bytes.
- Publication expiry is lazy. There is no Celery beat task. The public collection hides an overdue row before its status flips. Public detail and advertiser commands write the expiry.
- A personal child can be left with no manage grant when the parent's grant is revoked, because the cascade skips the last-manager guard.
- Inherited copies are taken only when the child is created. A grant added on the parent later is not copied.
- Pausing an already-expired mandate withdraws the live listings and then returns 409 for the pause itself.
- If the recorded advertiser is suspended, a colleague with `property.publish` can pause the listing but cannot resume it. They withdraw and create a new listing.
- There is no public approximate pin. Do not round the stored coordinate.
- The public query may still select `street_line` and `coordinates_set_at`. The serializer drops the street. The geography column is deferred.
- `listing.manage` is not granted. `property.view` is not granted.
- Overlapping listings of different types are allowed. One open listing per type is the constraint.
- Price and photograph edits are allowed only while the listing is a draft.
- AI may be labeled on a description. M5 does not call a model, store embeddings, or publish without the authorized workflow.
- The staff `expire-due` batch and several defensive error branches are implemented and are thinner in the coverage report than the main lifecycle.
- Passing tests are not a production launch. Hosting, backups, SMS, encryption of TOTP secrets, object storage, a penetration test, and a data-protection assessment are still open.

## Decision

READY FOR M6 WITH DOCUMENTED LIMITATIONS.
