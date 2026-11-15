# Milestone status

Updated 2026-11-11. This file records decisions already made. It is not a production launch.

| Milestone | Branch | Commit | Decision |
|---|---|---|---|
| M1 Foundation | `main` | `a1ab779db696e91f14472f5960629b4b7642afe2` | READY WITH DOCUMENTED LIMITATIONS |
| M2 Identity and authentication | `feature/identity-authentication` | `632f37806a24ee155df70b51819081d059ab7781` | READY FOR M3 WITH DOCUMENTED LIMITATIONS |
| M3 Organizations, memberships, and RBAC | `feature/organizations-rbac` | `0fbd0a29fe13f30662e2d981f6d01b3838db22e3` | READY FOR M4 WITH DOCUMENTED LIMITATIONS |
| M4 Property and location domain | `feature/property-location-domain` | `f088edc18667beb45b7482ba5aa2d1a0b9441016` | READY FOR M5 WITH DOCUMENTED LIMITATIONS |
| M5 Marketplace engine | `feature/marketplace-engine` | `553ed398d88c53edac92de637b84abd9dd1c3b23` | READY FOR M6 WITH DOCUMENTED LIMITATIONS |
| M6-A Media infrastructure | `feature/media-infrastructure` | not committed | IMPLEMENTED, NOT COMMITTED |

`AUTH_USER_MODEL` remains `identity.User`. M1 and M2 history has not been rewritten.

M2 limitations that M3 does not close: phone codes are emailed, TOTP secrets are signed rather than encrypted, Django admin does not require TOTP, `SameSite=Lax` needs a same-site proxy, failed logins do not set `security_restricted`, Celery has no application tasks, and GitHub Actions was not run from this workstation.

M3 limitations are listed in the M3 implementation record and still apply, except the stale-invitation reactivation that M5 closes. M4 limitations are listed in the M4 implementation record. M5 adds listings, mandates, asking prices, and media references. Payments, Channels, and an AI runtime are not built. Property and listing operations do not treat an organization registration reference or `phone_verified_at` as verification.

M6-A is implemented on `feature/media-infrastructure` and is not committed. It replaces accepted media references with server-verified objects in a private bucket. The historical Cloudinary sentence in the M5 record is unchanged. Payments, bookings, deposits, escrow, commissions, invoices, M-Pesa, and title verification remain out of scope. The M6-A record is `docs/engineering/M6A_IMPLEMENTATION.md`.
