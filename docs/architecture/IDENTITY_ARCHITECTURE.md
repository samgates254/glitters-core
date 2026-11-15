# Identity architecture

Status: M2 implemented on `identity.User`. Organization authorization is M3 and is documented in [ORGANIZATION_ARCHITECTURE.md](ORGANIZATION_ARCHITECTURE.md). The identity decision record is [ADR — Identity and authentication](decisions/ADR-identity-authentication.md).

## The user is a person, not a role

`identity.User` is the only account. The primary key is a UUID. Email is the login identifier. Password hashes use Argon2 first.

A person may later belong to several organizations and hold different business roles in each. M2 does not store an account type, an organization foreign key, or a role. `is_staff` and `is_superuser` exist for the Django admin bootstrap and for staff-only API schema access. They are not organization roles.

Future membership, invitation, and permission rows must use `settings.AUTH_USER_MODEL`. A membership grants nothing while `status` is not `active`.

## Account status

`status` is the source of truth. Saving a user sets `is_active` to true only for `active`.

| Status | Can sign in | How it is reached |
|---|---|---|
| `pending_verification` | No. Correct password returns 403 and asks for email verification. | Public registration |
| `active` | Yes, subject to MFA below. | Email verification, operator create, or password reset from `deactivated` / `security_restricted` |
| `suspended` | No. | Operator action. Password reset does not clear it. |
| `deactivated` | No. | The account holder, with the current password. |
| `security_restricted` | No. | Operator action. It is not applied automatically after failed logins. |

`IdentityBackend.get_user` still loads an inactive session so the API can return "This account cannot sign in." and clear the cookie. Password authentication itself continues to reject inactive accounts.

## Email

The stored email is the stripped, lowercased address. The unique constraint is on that value.

These responses do not reveal whether the address is registered:

- `POST /api/v1/auth/register/`
- `POST /api/v1/auth/email/verification/`
- `POST /api/v1/auth/password/reset/`

Unknown-email login and wrong-password login return the same 401 body. Each unknown-email attempt performs one Argon2 verification against a dummy hash.

Verification and reset tokens are `secrets.token_urlsafe(32)`. Only the SHA-256 hash is stored. A new token of the same purpose marks older unused tokens used. Email verification lasts 24 hours. Password reset lasts 1 hour. Both are single use. A weak replacement password is rejected before the reset token is consumed.

## Phone

Collection is optional. The stored form is E.164.

| Input | Stored |
|---|---|
| `0712 345 678`, `712345678`, `254712345678`, `00254712345678` | `+254712345678` |
| `+1 415 555 2671` | `+14155552671` |
| A national number outside that Kenyan pattern | Rejected. The client must send E.164. |

Unverified numbers may repeat and are not identity. Verified numbers are unique. The code is a 6-digit value, valid for 10 minutes, hashed with the user id and purpose. Five failures burn it. The code is emailed to the account. There is no SMS provider in M2.

## Credentials

### Browser

`POST /api/v1/auth/login/` with `"client": "web"` calls Django `login`, then `cycle_key()`, and stores `AuthSessionLink`. The session cookie is HTTP-only and `SameSite=Lax`. The CSRF cookie is readable by JavaScript on the same site so the client can set `X-CSRFToken`.

Identity views call `csrf_protect` because Django REST Framework marks its handlers CSRF-exempt. `GET /api/v1/auth/csrf/` sets the cookie. A missing or wrong token returns `{errors: {detail: CSRF verification failed.}}` and does not include Django's internal reason.

The supported browser deployment is same-site, with nginx presenting the Next.js app and this API under one site. A browser on port 3000 posting to port 8010 is cross-site. `SameSite=Lax` will not attach the session cookie to that cross-site request.

### Mobile

`"client": "mobile"` returns `access_token`, `refresh_token`, and `expires_in` (900 seconds). No browser session is created. The access token lasts 15 minutes. The refresh token lasts 30 days. Both are stored as SHA-256 hashes on `identity_api_credential`.

`POST /api/v1/auth/token/refresh/` rotates both secrets and keeps the same `family_id`. Presenting a refresh token that was already rotated revokes every credential in that family. A refresh token is not an access token.

Native applications store the two secrets in the operating system secure store. They still send `X-CSRFToken` from the CSRF endpoint, because the CSRF token is a request check and not the credential.

Logout with `Authorization: Bearer` revokes that access credential. `POST /api/v1/auth/revoke/` with `scope=others` revokes other browser sessions and all mobile credentials. `scope=all` also ends the current browser session. Password change keeps the current browser session, when one exists, and revokes the rest.

## MFA

TOTP is SHA-1, 6 digits, 30 seconds, with one step of clock skew. The secret is signed with Django's signer. It is not encrypted at rest.

| Account | Password-correct login |
|---|---|
| Ordinary account, no confirmed device | Browser session or mobile credential |
| Ordinary account, confirmed device | `mfa_required` and a 5-minute `challenge_token` |
| Staff or superuser, no confirmed device | `mfa_enrollment_required` and a 10-minute `enrollment_token`. No session and no mobile credential. |
| Staff or superuser, confirmed device | Same challenge as an ordinary account |

Enrollment returns the base32 secret and an `otpauth://` URI. Confirmation returns eight recovery codes once. Each code is stored as a hash of the user id plus the code and can be used once. Staff cannot disable MFA. Ordinary users disable it with the current password and a valid code.

Five wrong challenge codes burn the challenge token.

Django admin does not require a confirmed device. Any `/api/` request from a staff or superuser session without a confirmed device is rejected and the session is cleared. `/api/v1/health/` stays open.

## Profile and audit

`GET` and `PATCH /api/v1/auth/me/` are the only profile routes. The response is `id`, `email`, `first_name`, `last_name`, `status`, `phone_e164`, `email_verified`, `phone_verified`, `mfa_enabled`, and `is_staff`. Patch changes names only. Email, status, staff, and phone are not writable there. Phone uses the phone endpoints.

`identity_event` is append-only. Rows store the event type, an optional user, `REMOTE_ADDR` when it is a valid IP, and a truncated user agent. They do not store passwords, raw tokens, or the email that was attempted when the user does not exist. Token, credential, and MFA secret models are not in the Django admin. Operators can suspend or restore from the user admin. Those actions cannot suspend the operator who is signed in.

## API surface

All routes are under `/api/v1/auth/`. Error bodies are `{errors, request_id}`. Throttled requests return 429.

| Method | Path | Auth | Result |
|---|---|---|---|
| `GET` | `csrf/` | Public | Sets the CSRF cookie |
| `POST` | `register/` | Public | 202 generic |
| `POST` | `login/` | Public | Session, mobile tokens, MFA challenge, or enrollment |
| `POST` | `logout/` | Authenticated | Revokes the presented bearer token and ends the browser session |
| `POST` | `mfa/verify/` | Public, challenge token | Completes login |
| `POST` | `mfa/enroll/` | Session or enrollment token | Returns the TOTP secret once |
| `POST` | `mfa/confirm/` | Session or enrollment token | Confirms the device and returns recovery codes |
| `POST` | `mfa/disable/` | Authenticated | Ordinary accounts only |
| `GET`, `PATCH` | `me/` | Authenticated | Current user |
| `POST` | `email/verify/` | Public | Consumes the email token |
| `POST` | `email/verification/` | Public | 202 generic resend |
| `POST` | `password/reset/` | Public | 202 generic |
| `POST` | `password/reset/confirm/` | Public | Sets the password |
| `POST` | `password/change/` | Authenticated | Changes the password and revokes other credentials |
| `POST` | `token/refresh/` | Public, refresh token | Rotates the mobile family |
| `POST` | `revoke/` | Authenticated | `others` or `all` |
| `POST` | `phone/` | Authenticated | Stores an unverified number and emails a code |
| `POST` | `phone/verify/` | Authenticated | Confirms the code |
| `POST` | `deactivate/` | Authenticated | Requires the current password |

Default DRF authentication is bearer, then session. Bearer failures include `WWW-Authenticate: Bearer`, so a missing credential is 401 rather than a silent 403. Permission remains authenticated unless the view opts out.

## Throttles

`IDENTITY_THROTTLE` defaults to true. The test suite turns it off except for the throttle test.

| Scope | Rate | Key |
|---|---|---|
| `auth_login` | 10/minute | Client IP and email hash. Refresh uses this scope. |
| `auth_register` | 5/hour | Client IP |
| `auth_password_reset` | 5/hour | Client IP |
| `auth_verify` | 10/hour | Client IP |
| `auth_mfa` | 10/minute | Client IP |
| `auth_phone` | 5/hour | Client IP |

## Layers

Views validate with serializers and call services. Services own registration, login, tokens, passwords, phone, MFA, credentials, and status changes. Domain rules for normalization, status transitions, and public messages live in `apps.identity.domain`. Models do not send email or decide login. There is no selector module in M2 because the reads are direct.

## M3 handoff

M3 adds organizations, memberships, invitations, and roles as new tables. It does not add a user model and it does not read `is_staff` as an organization role. Access to an organization's resources requires an active user, a membership, and a permission check on the object. Those checks now live in `apps.organizations`.
