# M7-B tenancy HTTP API

Authenticated routes under `/api/v1/tenancy/`. Views discover leases and call the existing lease services. They do not reserve occupancy, hash contracts, or change status themselves.

This stack starts at lease execution `43e052af957158571667cbb381260dd5d5301d6f`. Wave 0, contract hashing, lifecycle services, and execution are ancestors. They are not merged. Draft pull requests stay blocked while GitHub requires a verified email.

## Authority

`leases.view`, `leases.prepare`, and `leases.approve` stay on owner, admin, and manager. `tenants.vet` is on those same roles. `leases.execute` and `leases.terminate` are on owner and admin only. `leases.manage` stays reserved.

An organization role is not a property grant. Drafts and approvals still require direct management or an active agreement whose granting user holds that grant. Activation and termination still require `verify_lease_execution_authority`. A direct owner does not need `leases.execute` or `leases.terminate`. An agent also needs the matching platform permission and `can_execute_leases` on the agreement.

A lease the caller cannot see returns 404. Anonymous calls return 401. Signature responses omit IP address and user agent. Click-to-sign evidence is an audit record, not a legal seal and not title.

## Routes

| Method | Path | Service |
|---|---|---|
| GET | `/api/v1/tenancy/leases/` | Visible leases only |
| POST | `/api/v1/tenancy/leases/` | `create_draft_lease` |
| GET | `/api/v1/tenancy/leases/<id>/` | Visible lease |
| GET | `/api/v1/tenancy/leases/<id>/parties/` | Parties on a visible lease |
| GET | `/api/v1/tenancy/leases/<id>/terms/` | Contractual terms |
| POST | `/api/v1/tenancy/leases/<id>/approve/` | `approve_lease_terms` |
| POST | `/api/v1/tenancy/leases/<id>/cancel/` | `cancel_lease` |
| GET | `/api/v1/tenancy/leases/<id>/signatures/` | Signature status, without network evidence |
| POST | `/api/v1/tenancy/leases/<id>/sign/` | `sign_lease` |
| POST | `/api/v1/tenancy/leases/<id>/activate/` | `activate_lease` |
| POST | `/api/v1/tenancy/leases/<id>/termination-requests/` | `request_lease_termination` |
| POST | `/api/v1/tenancy/leases/<id>/terminate/` | `terminate_lease` |
| GET | `/api/v1/tenancy/leases/<id>/history/` | Audit events for a visible lease |
| GET | `/api/v1/tenancy/leases/<id>/document/` | Document version and hashes |
| GET | `/api/v1/tenancy/leases/<id>/reservation/` | Reservation interval |
| GET | `/api/v1/tenancy/summaries/occupancy/` | Counts only. No tenant, address, or document |

There is no submitted state between draft and pending signature. Approval is the submission. A termination request records an event and does not change the lease. Execution is the terminate route.

The occupancy summary is the authorized contract for a later geospatial reader. It does not change God's Eye.

Migration `0005_lease_termination_requested` adds the `lease_termination_requested` audit action. Migration `0006_lease_cancelled` adds the `lease_cancelled` audit action. Neither migration changes lease columns or the occupancy exclusion constraint.

## Cancellation

Cancellation is allowed from `draft` and `pending_signature`. It changes status only. Rent, the contract digest, and the stored reservation end stay as they were. A cancelled reservation leaves the occupancy exclusion, so a later approval of another lease can use those dates. Executed leases cannot be cancelled. A party, or a repeated client key held by someone who cannot cancel, does not cancel the lease.

## Not in this branch

Renewal, open-ended periodic reservation, rendered-document storage, rental ledgers, and the tenancy portal remain separate follow-on work. Renewal must not rewrite a signed digest in place.
