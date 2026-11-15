# ADR — Identity and authentication

- Status: accepted
- Date: 2026-11-10
- Milestone: M2
- Builds on: [ADR 0004](../../decisions/0004-custom-user-model.md)

## Context

`identity.User` is already migrated. It has a UUID primary key, email as `USERNAME_FIELD`, and Argon2 as the first password hasher. Replacing `AUTH_USER_MODEL` would rewrite `identity.0001_initial` and every foreign key that points at it.

GLITTERS will serve buyers, sellers, tenants, landlords, agents, agencies, developers, managers, BnB hosts and guests, professional providers, and platform administrators. One person can later belong to several organizations. An account type must not be treated as an authorization decision.

Clients are a Next.js application, future Android and iOS applications, this Django REST Framework API, and later external integrations.

## Decision

Keep `identity.User` as the only user model. `AUTHENTICATION_BACKENDS` is `apps.identity.backends.IdentityBackend`, which still authenticates that model. There is no `auth.User` table and no second user model. Future business rows reference `settings.AUTH_USER_MODEL`.

### Email

Store the whole address after strip and lowercase. Uniqueness is on that stored value, so `Ada@Example.com` and `ada@example.com` are one account. Registration, resend, and password-reset responses use the same generic message whether or not the address exists. A duplicate registration of a still-pending account sends a fresh verification token and invalidates the previous one. A duplicate of any other account sends an "account already exists" notice with no token.

### Phone

Phone is optional at registration. Values are stored as E.164. A number without a country code is interpreted as Kenyan (`07…`, `01…`, or a 9-digit `7`/`1` number, and `254` / `00` prefixes). Every other country must be submitted as E.164, so Kenya is the local-entry convenience and not a permanent geographic limit.

An unverified number is not unique and is not proof of identity. A verified number is unique (`identity_user_verified_phone_unique`). Verification is a separate authenticated operation. The current delivery adapter emails the numeric code to the account email. That is not an SMS receipt and not handset proof. When one user verifies a number, other unverified claims of that number are cleared.

### Authentication protocol

Browser clients use the Django session cookie. It is HTTP-only and `SameSite=Lax`. Staging and production also set `Secure`. The CSRF cookie is not HTTP-only so a same-site browser client can copy it into `X-CSRFToken`. The session cookie stays HTTP-only. Long-lived credentials are not placed in browser `localStorage`.

DRF marks API views CSRF-exempt, so every identity view opts back in with `csrf_protect`. Unsafe identity calls, including mobile calls, send the CSRF token from `GET /api/v1/auth/csrf/`. The CSRF token is not a credential.

Native clients use an opaque access token (15 minutes) and a rotating refresh token (30 days). Only SHA-256 hashes are stored. Reuse of a revoked refresh token revokes the whole token family. Mobile apps store both secrets in the platform secure store (Android Keystore or iOS Keychain), not in a web view's `localStorage`.

Login calls `cycle_key()` before linking the session, so a pre-login session id cannot be reused.

A correct password on an unknown account and a wrong password on a known account return the same 401 body. The unknown-account path still runs one Argon2 check against a dummy hash.

### Account lifecycle

`status` is authoritative. `save()` sets `is_active` only when `status` is `active`.

| From | Self-service or verification may move to |
|---|---|
| `pending_verification` | `active` (email verification) |
| `active` | `suspended`, `deactivated`, `security_restricted` |
| `suspended` | `active` (operator only; password reset does not do this) |
| `deactivated` | `active` (password reset, because the inbox was proven) |
| `security_restricted` | `active` (password reset, same reason) |

Public registration creates `pending_verification`. `create_user` still defaults to `active` so operator and test fixtures do not have to verify email. `create_superuser` is active, staff, superuser, and email-verified.

Suspended accounts cannot complete password reset. Brute-force login does not automatically set `security_restricted`. That state is an operator action. Throttles slow abuse without locking the account out of the owner's control.

### MFA

Time-based one-time passwords follow RFC 6238: SHA-1, 30 seconds, 6 digits, one step of skew. SHA-1 is the authenticator default. The shared secret is what must stay confidential. The secret is sealed with `django.core.signing` and the salt `glitters.mfa.v1`. That is a signature, not encryption at rest.

Staff and superuser accounts do not receive a session or a mobile credential until a device is confirmed. A password-correct login returns a short-lived enrollment token. Accounts that already have a confirmed device receive a 5-minute challenge token. Eight recovery codes are shown once, stored only as hashes, and each code works once.

Privileged accounts cannot disable the last factor. Ordinary accounts can disable MFA with the current password and a current code.

Django admin remains a password bootstrap and does not enforce TOTP. `ActiveAccountMiddleware` does enforce TOTP on `/api/` for staff and superusers. Health probes are exempt.

### What this milestone does not decide

Organizations, memberships, invitations, organization roles, and tenant isolation were deferred to M3 and are now specified in [ADR — Organization and RBAC](ADR-organization-and-rbac.md). `is_staff` remains the platform-admin flag for Django admin and staff-only schema access. It is not an organization role. A membership must not grant access unless the user status is `active`. No `account_type` column exists.

## Consequences

M3 can add membership rows with a foreign key to `settings.AUTH_USER_MODEL` without a user-model migration.

Browser clients on a different site from the API, such as Next.js on port 3000 calling the API on port 8010, are cross-site. `SameSite=Lax` will not send the session cookie on those cross-site requests. The supported browser deployment is same-site through the nginx proxy.

Phone verification will need a real SMS adapter before a verified phone can be described as possession of a handset. Until then, `phone_verified_at` means the email inbox received the code.

## Alternatives considered

- JWT access tokens in `localStorage` for the browser. Rejected. Any script that can read `localStorage` can replay the credential. The browser gets an HTTP-only session cookie instead.
- A second user model or Django's `auth.User` for administrators. Rejected. It would split identity and force a destructive swap of `AUTH_USER_MODEL`.
- Making phone required and unique at registration. Rejected. An unverified number is not identity, and requiring it blocks email-only sign-up.
- Automatic lockout to `security_restricted` after failed logins. Rejected. An attacker who knows an email could deny the owner access. Throttles are the automatic control.
- Adding `django-otp`, `pyotp`, or `phonenumbers`. Rejected for this milestone. The TOTP and E.164 rules in use are small enough to live in the identity app without a new dependency.
