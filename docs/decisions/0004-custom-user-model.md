# ADR 0004 — Custom user before the first migration

- Status: accepted
- Date: 2026-11-10

## Context

Django can swap `AUTH_USER_MODEL` safely only before the first migration that creates user relations. Waiting for the authentication milestone would freeze the built-in user table.

## Decision

Create `identity.User` now.

- UUID primary key.
- Email is `USERNAME_FIELD`, stored trimmed and lowercased.
- `AbstractBaseUser` plus `PermissionsMixin`.
- No organization foreign key.
- No profile, MFA, or credential-recovery fields yet.

Authentication endpoints were a later milestone when this ADR was accepted. M2 kept this model and added them in `identity.0002_identity_authentication`. See [ADR — Identity and authentication](../architecture/decisions/ADR-identity-authentication.md).

## Consequences

The first migration owns the user table shape. Email comparison is case-insensitive for the whole address, which is a product choice rather than a strict reading of local-part case sensitivity. Login, reset, and MFA still have to be designed and tested.
