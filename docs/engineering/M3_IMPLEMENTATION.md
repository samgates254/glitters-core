# M3 implementation

Date: 2026-11-11. Branch: `feature/organizations-rbac`. Implementation commit: `0fbd0a29fe13f30662e2d981f6d01b3838db22e3`. Parent: `632f37806a24ee155df70b51819081d059ab7781`.

## What shipped

`apps.organizations` adds organizations, memberships, invitations, organization audit events, and organization-scoped RBAC on the existing PostgreSQL database. `identity.User` is unchanged. Identity routes and tests are unchanged in behaviour. No package was added. Celery still has no application tasks.

The architecture is in:

- `docs/architecture/ORGANIZATION_ARCHITECTURE.md`
- `docs/architecture/RBAC_ARCHITECTURE.md`
- `docs/architecture/MULTI_TENANCY_ARCHITECTURE.md`
- `docs/architecture/decisions/ADR-organization-and-rbac.md`

## Schema

Migration `organizations.0001_initial` creates:

- `organization`
- `organization_membership`, with one row per user and organization and at most one active owner
- `organization_invitation`, token hash only, one pending row per organization and email, role cannot be `owner`
- `organization_event`, append-only

`makemigrations --check --dry-run` reported no pending changes after that migration. `manage.py check` reported no issues.

## API

Routes are under `/api/v1/` and are listed in `docs/api/V1.md`. OpenAPI includes them. Unsafe organization views opt back into CSRF. Errors stay `{errors, request_id}`.

## Verification on 2026-11-11

Commands were run from `/home/mr-robot/Projects/glitters-core` with the project virtualenv:

- `ruff check .` passed.
- `ruff format --check .` passed. 112 files were already formatted.
- `python manage.py makemigrations --check --dry-run` reported no changes.
- `python manage.py check` reported no issues.
- `pytest --cov=apps --cov=config --cov-report=term-missing` reported **96 passed** in 21.83 seconds. Coverage of `apps` and `config` was **94%**.
- `uv run pip-audit --progress-spinner off` reported `No known vulnerabilities found`.

The M2 suite was 68 tests. Those tests still run. The additional tests cover organization create, list, update, isolation, membership uniqueness, multiple organizations, invitations (accept, expiry, revoke, resend, replay, email mismatch, duplicate, unverified email), the role matrix, ownership transfer, last-owner protection, suspended accounts, memberships, and organizations, cross-organization ids, staff versus organization roles, CSRF, and concurrent transfer, invitation, and suspension.

This workstation did not push the branch and did not run GitHub Actions.

## Limitations

- A registration reference does not prove that the business exists.
- Self-service organizations start `active`. Staff review is a later status change, not a gate on creation.
- Phone codes are still emailed. `phone_verified_at` is not handset proof and is not read by organization authorization.
- TOTP secrets remain signed, not encrypted. Django admin still does not require TOTP. API staff calls do.
- `SameSite=Lax` still needs a same-site proxy for a browser on another site.
- Failed logins are throttled and do not set `security_restricted`.
- Invitation expiry is evaluated on use. Nothing sweeps expired rows on a schedule.
- `organization.manage_billing` and the reserved property, listing, lead, lease, finance, hospitality, and service-request permissions are not attached to any module.
- Staff can change any organization's status and learn that status. They cannot read the profile or act as a member without a membership.
- There is exactly one active owner. A co-owner model would be a new decision.
- The organization admin is inspect-only. It is not a second authorization path.
- AI access is a convention. No assistant runtime exists.
- Tenant isolation is application-level in one database. There is no PostgreSQL row-level security policy.
- Passing tests are not a production launch. Hosting, backups, SMS, encryption of TOTP secrets, a penetration test, and a data-protection assessment are still open.

## Decision

READY FOR M4 WITH DOCUMENTED LIMITATIONS.

M4 should add the property and location records on an organization foreign key, then grant `property.*` from the existing registry inside the property services. It should not add a user model, a second database, or a microservice.
