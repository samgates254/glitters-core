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
| POST | `/api/v1/tenancy/leases/<id>/renewals/` | `initiate_lease_renewal` |
| POST | `/api/v1/tenancy/leases/<id>/renewals/abandon/` | `abandon_lease_renewal` |
| GET | `/api/v1/tenancy/leases/<id>/signatures/` | Signature status, without network evidence |
| POST | `/api/v1/tenancy/leases/<id>/sign/` | `sign_lease` |
| POST | `/api/v1/tenancy/leases/<id>/activate/` | `activate_lease` |
| POST | `/api/v1/tenancy/leases/<id>/termination-requests/` | `request_lease_termination` |
| POST | `/api/v1/tenancy/leases/<id>/terminate/` | `terminate_lease` |
| GET | `/api/v1/tenancy/leases/<id>/history/` | Audit events for a visible lease |
| GET | `/api/v1/tenancy/leases/<id>/document/` | Document version and hashes, without bytes |
| GET | `/api/v1/tenancy/leases/<id>/document/content/` | Stored bytes for a visible lease |
| GET | `/api/v1/tenancy/leases/<id>/reservation/` | Reservation interval |
| GET | `/api/v1/tenancy/summaries/occupancy/` | Counts only. No tenant, address, or document |

There is no submitted state between draft and pending signature. Approval is the submission. A termination request records an event and does not change the lease. Execution is the terminate route.

The occupancy summary is the authorized contract for a later geospatial reader. It does not change God's Eye.

Migration `0005_lease_termination_requested` adds the `lease_termination_requested` audit action. Migration `0006_lease_cancelled` adds the `lease_cancelled` audit action. Migration `0007_lease_renewal_abandoned` adds the `lease_renewal_abandoned` audit action. None of these migrations change lease columns or the occupancy exclusion constraint.

## Cancellation

Cancellation is allowed from `draft` and `pending_signature`. It changes status only. Rent, the contract digest, and the stored reservation end stay as they were. A cancelled reservation leaves the occupancy exclusion, so a later approval of another lease can use those dates. Executed leases cannot be cancelled. A party, or a repeated client key held by someone who cannot cancel, does not cancel the lease.

## Renewal

Renewal is a new lease row with `supersedes_lease` set. The predecessor moves from `active` to `under_renewal`. Its rent, dates, document version, and contract digest stay as signed. The successor starts at document version 1 and does not reserve occupancy until approval. Approval still runs the overlap check, so a successor that overlaps the predecessor is rejected while that reservation stands. Abandoning a renewal returns the predecessor to `active` only after every open successor has been cancelled. It does not terminate the predecessor.

## Open-ended periodic reservation

A residential or commercial periodic lease may omit `term_end_date`. Approval reserves occupancy with a null exclusive end. PostgreSQL stores that as an unbounded half-open range, so the existing GiST exclusion still rejects a later lease on the same property. A finite reservation cannot be moved later or cleared. Termination sets the exclusive end to the day after the termination date and leaves the contract digest and null term end unchanged. The released dates can then be reserved.

## Rendered documents

Approval stores the exact rendered bytes on the server, one row per lease document version. The stored locator is `tenancy://leases/<id>/documents/<version>`. A client-supplied URI is not the retrieval path. `GET .../document/` returns version and hashes. `GET .../document/content/` returns the bytes only when the caller can see the lease and the stored SHA-256 matches both the row and the lease. A mismatch returns 409 and no document body. Click-to-sign remains an audit record, not a PDF/A seal.

## Not in this branch

Rental ledgers and the tenancy portal remain separate follow-on work.
