# Security architecture

Status: baseline controls, the M2 identity protocol, M3 organization RBAC, and M4 property and location authorization are implemented and tested. Listing and payment object permissions are not.

## In place

| Control | Implementation |
|---|---|
| Secret handling | `DJANGO_SECRET_KEY` comes from the environment. `.env` is gitignored. Staging and production do not read `.env`. |
| Debug guard | Staging and production refuse `DJANGO_DEBUG=true`. |
| Host guard | Staging and production refuse an empty host list and `*`. |
| Password storage | Argon2 is the first hasher. Django's password validators run before a user is saved. |
| Privilege creation | `create_user` cannot set `is_superuser`. Public registration cannot set `is_staff`. |
| Account status | `status` controls `is_active`. Suspended, deactivated, pending, and security-restricted accounts cannot use the API. |
| Browser session | HTTP-only session cookie, `SameSite=Lax`, rotated at login. CSRF is enforced on identity writes. The CSRF cookie is readable so a same-site client can set `X-CSRFToken`. |
| Mobile credentials | Opaque access and rotating refresh tokens. Only SHA-256 hashes are stored. Refresh replay revokes the family. |
| Email and phone tokens | High-entropy or numeric secrets, stored hashed, expiring, single use. |
| MFA | TOTP for confirmed devices. Staff cannot obtain an API session until enrollment is confirmed. Recovery codes are hashed and single use. |
| Throttles | Login, registration, reset, verification, MFA, and phone endpoints have dedicated rates. |
| Audit | `identity_event`, `organization_event`, and `property_event` are append-only. They store no secrets, raw invitation tokens, or exact coordinates. |
| Organization RBAC | Membership, active account, active organization, and a central permission registry. A role does not cross organizations. `is_staff` is not an organization role. |
| Property access | Personal rows require `property_access`. Organization rows require an active membership or an explicit grant. Writes require `property.create`, `property.update`, or `property.archive`, or a `manage` grant. A property id is only a lookup key. |
| Coordinate privacy | List and search serializers have no coordinate field. Detail includes the point only for a caller who can update the property. |
| Invitations | High-entropy tokens, SHA-256 at rest, seven-day expiry, one-time acceptance, verified email match. |
| Enumeration | Registration, resend, and reset return one generic body. Unknown email and wrong password return one 401 body. |
| Default API access | DRF requires an authenticated user unless a view opts out. Bearer challenges advertise `WWW-Authenticate: Bearer`. |
| Health probes | Public, unthrottled, and free of dependency details that would expose a secret. Failures say `unavailable`. |
| API schema | Public only while `DEBUG` is on. Otherwise a staff session is required. |
| Django admin | Staff login required. This is not the Control Center. |
| CSRF | Enabled for identity, organization, property, and location writes. Session authentication also checks CSRF after login. |
| CORS | Explicit origins. `CORS_ALLOW_ALL_ORIGINS` is false. |
| Session cookies | HTTP-only, SameSite `Lax`. Secure flag is on in staging and production. |
| Security headers | `X-Content-Type-Options`, frame denial, referrer policy. HSTS is configured for deployed settings. |
| Transport | Deployed settings redirect to HTTPS and trust `X-Forwarded-Proto` from the proxy. |
| Rate limits | Anonymous and user DRF throttles are configured. Health probes are exempt so orchestrators are not locked out. |
| Celery content | JSON only. Pickle is not accepted. |
| Request ids | Caller-supplied ids are accepted only when they match a safe pattern. |
| Error shape | API errors include `request_id` and do not add a second stack trace in the body. |
| Dependency audit | `pip-audit` is installed and wired into CI. |
| Database exposure | The development database port is bound to `127.0.0.1`. |

Tests cover email normalization, hashed passwords, rejected weak passwords, superuser creation, public health, staff-only schema when debug is off, the admin redirect, the deployed settings guards, the identity flows in `tests/integration/test_identity_api.py` and `tests/security/test_identity_security.py`, the organization flows in `tests/integration/test_organizations_api.py` and `tests/security/test_organizations_security.py`, the property and location flows in `tests/integration/test_properties_api.py`, `tests/integration/test_locations_api.py`, and `tests/security/test_properties_security.py`, and the marketplace flows in `tests/integration/test_marketplace_api.py`, `tests/integration/test_marketplace_authorization.py`, `tests/integration/test_grant_provenance.py`, `tests/security/test_marketplace_privacy.py`, and `tests/security/test_invitation_reactivation.py`.

## Not in place

These are required before the platform holds customer or payment data. They are not claimed as done.

- Object permissions for leases and payments. Listing and property object checks are in place. Profile access remains `/api/v1/auth/me/`.
- A public approximate map pin. Public listings disclose area and locality names. Exact coordinates stay on the authorized property detail response.
- Hiding one organization's property from another member of that same organization. Members and viewers can read the organization's properties.
- Legal-title, business-registration, and agent-licence verification. A party role is a claim. A listing mandate is advertising authority. Platform review is not either of those.
- PostgreSQL row-level security. Property isolation is application-level, including the personal-access branch.
- SMS delivery. Phone codes go to the account email.
- Encryption of TOTP secrets at rest. They are signed.
- Cross-site browser sessions. `SameSite=Lax` requires the app and API to share a site.
- TOTP on the Django admin bootstrap. The API enforces it for staff.
- Automatic movement to `security_restricted` after failed passwords. Throttles are the automatic control.
- Private document bytes in object storage. M5 accepts a PDF reference and refuses to attach it to a public listing. It does not fetch or store the file.
- Webhook signature verification and replay protection.
- Idempotency keys for payments.
- Financial audit. Organization audit exists. Payment and ledger audit do not.
- Encryption-at-rest guarantees from a hosting provider.
- A completed Data Protection Act assessment.
- Production secret management, image scanning in a registry, and a penetration test.

## Authorization rule

The frontend is not a security boundary. Organization, property, and listing operations are authorized on the server against the authenticated user, the account status, the organization or personal access path, the organization status, the membership, the role permission, the mandate, and the object. A client-supplied organization id, property id, or mandate id is only a key. A party role does not grant ownership, title, or API access. A listing created by an agent does not grant that agent ownership. Publication does not infer legal verification from an active account or an active organization.

M5 refuses to restore a removed or suspended membership from an old invitation. A fresh invitation returns the membership to `invited`. Acceptance is a separate step. Details are in [LISTING_MANDATES.md](LISTING_MANDATES.md).

## Identity documents and financial data

When those records are added they will be treated as sensitive: restricted queries, private object storage, short-lived download URLs, and audit of access. They will not be served from the public media directory.

## Production settings fail closed

`config.settings.production` raises `ImproperlyConfigured` unless debug is off, the secret is at least 50 characters and does not start with `change-me`, and allowed hosts are explicit. The container image sets this settings module by default. The development Compose profile overrides it back to development.

## Threats the foundation already considers

| Threat | Current response |
|---|---|
| Accidental debug deployment | Settings import fails. |
| Placeholder secret | Settings import fails outside development. |
| Open CORS | Disabled in configuration and covered by a test. |
| Log injection through a request id | Unsafe header values are discarded. |
| Mass assignment of superuser | Manager rejects it. Registration ignores `is_staff`. Profile patch ignores email, status, and staff. |
| Credential stuffing | Login throttle plus a shared failure body and a dummy Argon2 check. |
| Session fixation | Login cycles the session key. |
| Token replay | Email, reset, and phone tokens are single use. A rotated refresh token revokes its family. |
| Staff API without MFA | Middleware and bearer authentication reject it. |
| Credential in git | `.env` is ignored. The Compose password is a localhost development default and must not be reused. |
| Pickle gadget through Celery | JSON serializer only. |
| Property IDOR | Hidden personal and cross-organization properties return 404. Writes repeat `decide` inside the service. |
| Coordinate leak through a second serializer | List and search shapes omit the point. Detail includes it only after the same authorization decision. |

Cross-organization membership lookups are tested. Webhook replay and payment duplication cannot be tested until those features exist. They are acceptance tests for the milestones that introduce them.
