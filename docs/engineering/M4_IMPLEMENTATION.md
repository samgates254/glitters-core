# M4 implementation

Date: 2026-11-11. Branch: `feature/property-location-domain`. Implementation commit: `f088edc18667beb45b7482ba5aa2d1a0b9441016`. Parent: `48999b01f8812732603f5e9d51ead1c8aa59b661`.

## What shipped

`apps.locations` and `apps.properties` add the canonical property and location domain on the existing PostgreSQL and PostGIS database. `identity.User` is unchanged. Organization routes still use the M3 permission registry. M4 grants `property.create`, `property.update`, and `property.archive` to owner, admin, and manager. `property.publish` stays reserved. No package was added. Celery still has no application tasks. There is no listing table.

The architecture is in:

- `docs/architecture/PROPERTY_ARCHITECTURE.md`
- `docs/architecture/LOCATION_ARCHITECTURE.md`
- `docs/architecture/decisions/ADR-property-domain.md`
- `docs/architecture/decisions/ADR-location-model.md`

## Tenancy

A property is `personal` or `organization`. Personal rows have a null managing organization and are visible only through `property_access`. Organization rows require an active organization and an active membership, or an explicit grant while that organization is active and the caller has no non-active membership there. The managing organization is not the legal owner. Context cannot be changed in M4.

## Schema

`locations.0001_initial` creates `country`, `administrative_level`, `administrative_area`, `locality`, and `address`. It depends on `core.0001_enable_postgis`.

`locations.0002_seed_countries` seeds the level ladders for Kenya, Uganda, Tanzania, and Rwanda. It does not seed gazetteer areas.

`properties.0001_initial` creates `property`, `property_location`, `property_attribute`, `property_party`, `property_access`, and `property_event`. `property_location.coordinates` is a geography point, SRID 4326, with the GiST index created by the GeoDjango schema editor. A following `RunSQL` check constrains that point to WGS84.

## API

Routes are under `/api/v1/` and are listed in `docs/api/V1.md`. OpenAPI includes them. Unsafe property and location views opt back into CSRF. Errors stay `{errors, request_id}`.

## Verification

Commands were run from `/home/mr-robot/Projects/glitters-core` with the project virtualenv on 2026-11-11:

- `ruff check .` passed.
- `ruff format --check .` passed. 164 files were already formatted.
- `python manage.py makemigrations --check --dry-run` reported no changes.
- `python manage.py check` reported no issues.
- `pytest --cov=apps --cov=config --cov-report=term-missing` reported **127 passed** in 34.83 seconds. Coverage of `apps` and `config` was **91%**.
- `uv run pip-audit --progress-spinner off` reported `No known vulnerabilities found`.

The M2 suite was 68 tests at 93% coverage. The M3 suite was 96 passed at 94% coverage, with ruff and pip-audit clean, at implementation commit `0fbd0a29fe13f30662e2d981f6d01b3838db22e3`. Those tests still run. Intentional updates assert the new property grants. They do not weaken identity or organization security tests.

M4 tests cover property create, update, archive, lifecycle, subtypes, characteristics, hierarchy, personal isolation, organization isolation, participants, permission denial, location ladders, invalid administrative chains, coordinate validation, PostGIS `ST_DWithin` and the GiST index, coordinate privacy, relationship revocation, pagination, CSRF, suspended accounts, memberships, and organizations, staff versus property access, and concurrent archive.

This workstation did not push the branch and did not run GitHub Actions.

## Limitations carried forward

- TOTP secrets are signed with salt `glitters.mfa.v1`, not encrypted at rest.
- Django admin does not require TOTP. Property admin is inspect-only and excludes the coordinate field. It is not a second authorization path.
- Phone codes are emailed. `phone_verified_at` is not handset proof and is not read by property authorization.
- `SameSite=Lax` still needs a same-site proxy. A browser on another site, such as a Next.js origin calling the API on another port, is unsupported.
- Failed logins are throttled and do not set `security_restricted`.
- Self-service organizations start `active`. A registration reference is not legal verification. Property services do not treat it as one.
- Tenant isolation is application-level. There is no PostgreSQL row-level security policy.
- Invitation expiry is lazy.
- Accepting an old invitation can still reactivate a removed or suspended membership. Property services still require an active membership at request time. M4 does not change that invitation behaviour.

## Limitations introduced or left open by M4

- No listing, price, deposit, commission, booking, or payment behaviour.
- No process sets a party to `verified`. GLITTERS does not verify legal title and does not call a land registry.
- `property.publish` is granted to nobody.
- Members and viewers of an organization can read all of that organization's properties, including exact-coordinate absence and the `has_coordinates` flag. They cannot see the point.
- A suspended member receives 403 on a property they previously could see, which confirms that the id exists.
- There is no public approximate pin.
- Gazetteer areas and localities are not editable through the API. Name search is `ILIKE`, not a trigram index.
- Management context is immutable. Archive is terminal. Active does not return to draft.
- Personal child grants are copied at creation and do not follow later parent revocations.
- Nearby distance is rounded to 100 metres. Combined with a small radius, that still narrows a point.
- An organization may manage a property addressed in another country. `Organization.country` is not compared with the address country.
- AI access is a convention. No assistant, embedding store, or valuation model exists.
- Passing tests are not a production launch. Hosting, backups, SMS, encryption of TOTP secrets, a penetration test, and a data-protection assessment are still open.

## Decision

READY FOR M5 WITH DOCUMENTED LIMITATIONS.

M5 should add the marketplace listing lifecycle that references `property.id`, and should grant `property.publish` only inside that module. It should not redesign the property tables, merge listing status into property status, or treat a party claim as title.
