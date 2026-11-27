# Colossus Domain Integration — Backoffice Slice

The backoffice app is an authorization and read-only aggregation layer. It does not
replace canonical domain models or own their operational state.

## Canonical records and dashboard sources

| Dashboard area | Canonical source | Current interpretation |
|---|---|---|
| Managed properties | `properties.Property` | Count and status totals filtered by managing organization. |
| Marketplace listings | `marketplace.Listing` | Count and status totals for listings advertised by, or attached to properties managed by, an authorized organization. |
| Marketplace enquiries | `marketplace.Enquiry` | Count and status totals only; contact details and messages are excluded. |
| Rental agreements | `tenancy.Lease` | Total, active, and under-renewal agreement counts. No lease state is duplicated or changed. |
| Rental finance | `tenancy.RentInvoice`, `tenancy.RentAllocation` | Invoice demands, net bookkeeping allocations after reversals, and overdue balances in minor currency units. Not verified cash collections. |
| Construction projects | `construction.ConstructionProject` | Project count and lifecycle status totals. |
| Construction costs | `construction.ProjectBudget`, `construction.CostRecord` | Approved budgets and commitment, actual, and forecast inputs grouped by currency. Not a second accounting ledger. |

Every dashboard metric includes its source model, effective organization scope, and
latest source update time (`as_of`). An empty or unavailable source is not replaced
with fabricated business performance.

## Staff roles and scope

Role values are defined in `apps/backoffice/domain.py`. A grant has one role and one
scope: either platform-wide or one organization. Grants expire or are revoked without
deleting history. Grant and revoke events are append-only.

Dashboard access requires an active account, confirmed MFA, and at least one active
grant. Only an MFA-confirmed Django superuser may administer grants or read the role
audit. A platform-scoped grant expands only the widgets allowed to that particular
role. For organization-scoped grants, each widget aggregates only organizations
associated with the roles that authorize that widget; scopes from different roles are
never combined to widen a more sensitive widget.

Organization scope is based on active organization records. The API does not create
`PropertyAccess`, change a legal owner, or grant access to tenant-level personal data.

## Integration boundary and gaps

- This slice reads domain ORM models directly for aggregates; it does not yet provide
  write adapters or published service contracts for cross-domain workflows.
- Revenue, verified payment receipts, occupancy/vacancy, arrears from a reconciled
  payment ledger, commissions, maintenance backlog, and customer-support metrics are
  not currently presented because this slice does not have authoritative definitions
  and sources for them.
- No tenancy lifecycle actions, God's Eye processing, spatial writes, construction
  execution actions, or payment processing are implemented here.
- The app is registered in Django settings and mounted at `/api/v1/backoffice/`.
  Production rollout, frontend navigation, and combined full-suite validation remain
  separate integration work.
