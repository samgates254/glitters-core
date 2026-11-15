# M2 identity implementation

Date: 2026-11-10. Branch: `feature/identity-authentication`. The frozen M1 commit `a1ab779db696e91f14472f5960629b4b7642afe2` is the parent. It was not amended.

## What shipped

Identity and authentication on the existing `identity.User` model: registration, login, logout, current-user profile, email verification, password reset and change, session and mobile-credential revocation, account status enforcement, login throttling, audit events, optional phone collection, and TOTP MFA with recovery codes.

No organization, listing, payment, Channel, or business Celery task was added. No package was added. `uv.lock` is unchanged.

## How to verify

From `/home/mr-robot/Projects/glitters-core` with the project virtualenv active and the development containers up:

```bash
python manage.py migrate
python manage.py makemigrations --check --dry-run
python manage.py check
ruff check .
ruff format --check .
pytest --cov=apps --cov=config --cov-report=term-missing
uv run pip-audit --progress-spinner off
```

Evidence from this implementation, on 2026-11-10:

- `manage.py check` reported no issues.
- `makemigrations --check --dry-run` reported no changes.
- `migrate` applied `identity.0002_identity_authentication`.
- `ruff check` and `ruff format --check` passed.
- `pytest` reported 68 passed.
- Coverage of `apps` and `config` was 93%.
- `pip-audit` reported no known vulnerabilities.

The development `check --deploy` warnings from M1 (W004, W008, W012, W016, W018) still apply to the development settings module. They are not production settings.

## Schema

Migration `identity.0002_identity_authentication` depends on `identity.0001_initial` and `auth.0012`. It does not edit `0001`.

New columns on `identity_user`: `status`, `phone_e164`, `phone_verified_at`, `email_verified_at`, plus check constraints and a partial unique index for verified phones.

New tables, all pointing at `settings.AUTH_USER_MODEL`:

| Table | Purpose |
|---|---|
| `identity_token` | Hashed one-time tokens |
| `identity_api_credential` | Hashed mobile access and refresh secrets |
| `identity_session` | Browser session key linked to a user |
| `identity_mfa_device` | Signed TOTP secret |
| `identity_recovery_code` | Hashed recovery code |
| `identity_event` | Append-only audit row |

There is still no `auth_user` table.

## Tests that protect the security claims

The new tests cover registration and duplicate email, email normalization, login success and the shared failure body, logout, current-user access, Argon2 hashing, password reset including a weak password that does not burn the token, email verification, expiry, replay, suspension, deactivation, phone normalization and verification, throttling, CSRF, staff MFA before an API session, recovery-code reuse, cross-user bearer access, and audit rows that do not contain the password or the raw token.

Existing M1 tests were not removed or weakened. The staff schema test now confirms an MFA device first, because a staff session without MFA is no longer allowed to read `/api/` when `DEBUG` is off.

## Known limitations

- Phone codes are delivered by email. `phone_verified_at` is not proof that a handset received an SMS.
- TOTP secrets are signed, not encrypted at rest.
- Browser sessions are `SameSite=Lax`. A Next.js origin on another port is cross-site and will not send the session cookie. Use the same-site nginx proxy.
- Django admin login does not require TOTP. API access for staff does.
- Failed logins are throttled. They do not move the account to `security_restricted`.
- Celery still has no application tasks, and the beat schedule is still empty.
- This session did not push the branch and did not run GitHub Actions.
- Organization membership was out of scope for this milestone. M3 is recorded in `docs/engineering/M3_IMPLEMENTATION.md`.

## Decision

READY FOR M3 WITH DOCUMENTED LIMITATIONS.

M3 can add organization membership with a foreign key to `settings.AUTH_USER_MODEL`. It must not replace `identity.User`.
