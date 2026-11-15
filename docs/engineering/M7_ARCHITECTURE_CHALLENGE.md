# M7 Architecture Challenge & Independent Design Review

**Document Path**: `docs/engineering/M7_ARCHITECTURE_CHALLENGE.md`
**Milestone Target**: Milestone 7 (Property Management Operating System)
**Date**: November 12, 2026
**Reviewer Role**: Independent Principal Database Architect & Domain-Driven Design (DDD) Reviewer
**Subject Document**: [M7_PROPERTY_MANAGEMENT_ARCHITECTURE.md](file:///home/mr-robot/Projects/glitters-core/docs/engineering/M7_PROPERTY_MANAGEMENT_ARCHITECTURE.md)
**Codebase Baseline**: `glitters-core` at commit `7f61a98` (M6-A / M6-B working tree)
**Review Mandate**: Rigorous architectural challenge, failure-mode discovery, and database invariant validation prior to M7 implementation.

---

## 1. Executive Summary & Review Verdict

The proposed architecture in [M7_PROPERTY_MANAGEMENT_ARCHITECTURE.md](file:///home/mr-robot/Projects/glitters-core/docs/engineering/M7_PROPERTY_MANAGEMENT_ARCHITECTURE.md) represents a commendable and ambitious expansion of GLITTERS CORE. Its alignment with the modular monolith pattern, its preservation of `identity.User` as the sole account entity, and its conceptual separation between physical assets (`apps.properties`), contractual occupancy (`apps.tenancy`), physical upkeep (`apps.operations`), and cash ledgers (`apps.finance`) are fundamentally sound.

However, an adversarial database architecture and DDD audit reveals **critical design flaws and boundary leaks** that must be resolved prior to authoring models or migrations. Most notably:
1. **Lease Temporal Integrity (BLOCKER)**: The proposed partial unique constraint on active leases (`status="ACTIVE"`) is structurally incapable of preventing overlapping future leases, completely disables commercial pre-leasing, and fails to model early lease terminations without date collision.
2. **Occupancy Consistency & Split-Brain Risk (HIGH)**: Denormalizing `occupancy_status` and `current_lease_id` onto `UnitOperationalProfile` creates a dual source of truth with `Lease.status`, guaranteeing state desynchronization under concurrent updates or background job failures.
3. **Property Management Authority Hijacking (BLOCKER)**: Binding `ManagementAgreement` to an unverified `PropertyParty` claim permits unauthorized management companies to hijack operational authority over personal properties without cryptographic proof of ownership.
4. **Shadow Ledger Antipattern in Tenancy (HIGH)**: Placing `paid_amount_minor` and `status="PAID"` directly on `RentScheduleEntry` turns `apps.tenancy` into an un-reconciled shadow cash ledger, violating the financial integration boundary.
5. **Contractor PII Exposure & Cross-Tenant Data Leak (HIGH)**: The work order model leaks tenant PII (phone, email) to third-party contractor organizations and lacks bounded token expiration and cross-organization isolation rules.

Below is the exhaustive, evidence-based challenge matrix and required architectural corrections.

---

## 2. Review Area 1 — Lease Temporal Integrity

### Finding 1.1: Partial Unique Constraint Fails to Prevent Overlapping Leases (BLOCKER)
- **Proposed in Spec**: `UniqueConstraint(fields=["property"], condition=Q(status="ACTIVE"), name="one_active_lease_per_property")` ([M7 Spec Section 8.2](file:///home/mr-robot/Projects/glitters-core/docs/engineering/M7_PROPERTY_MANAGEMENT_ARCHITECTURE.md#L484-L485)).
- **Vulnerability / Failure Mode**:
  1. A partial unique constraint on `status="ACTIVE"` only enforces that no two leases have `status="ACTIVE"` *at the exact same database instant*. It evaluates zero temporal logic on `start_date` and `end_date`.
  2. **The Pre-Leasing Blindspot**: In residential and commercial leasing, a property manager must pre-lease a unit 30 to 60 days before the incumbent tenant vacates. If Lease A is `ACTIVE` (ending Dec 31) and Lease B is created and signed for next year (Jan 1 to Dec 31, `status="PENDING_SIGNATURE"` or `status="APPROVED"`), a naive status check allows an overlapping Lease C (Nov 1 to Oct 31) to be signed concurrently because Lease C is not yet `ACTIVE`!
  3. **Early Termination Collision**: If Lease A (originally Jan 1 to Dec 31) is terminated early on March 15 and moves to `status="TERMINATED_EARLY"`, the partial unique constraint releases the property. If Lease B is subsequently signed starting March 16, but Lease A's stored `end_date` remains Dec 31, any date-range reporting query across leases will report two overlapping contracts for the same unit.
- **DDD & Relational Remedy**:
  1. Standardize on **half-open interval semantics**: `[start_date, end_date)` where the interval includes the start date at 00:00:00 and excludes the end date at 00:00:00. This mathematically guarantees that an outgoing tenant vacating on March 15 and an incoming tenant moving in on March 15 do not overlap.
  2. Enforce temporal exclusion at the PostgreSQL engine level using a **PostgreSQL GiST Exclusion Constraint** on `daterange`:
     ```sql
     -- Requires btree_gist extension in PostgreSQL
     ALTER TABLE tenancy_lease
     ADD CONSTRAINT exclude_overlapping_active_leases
     EXCLUDE USING gist (
         property_id WITH =,
         daterange(start_date, effective_end_date, '[)') WITH &&
     )
     WHERE (status IN ('ACTIVE', 'PENDING_SIGNATURE', 'UNDER_RENEWAL'));
     ```
  3. Introduce `effective_end_date`: When a lease is terminated early, the service must atomically set `effective_end_date = termination_date` while preserving the historical contractual term `end_date` for audit and early-termination penalty calculations.

### Finding 1.2: Missing Database Extension Dependency (`btree_gist`) (HIGH)
- **Vulnerability**: In PostgreSQL, standard GiST indexes natively support spatial geometries and range types (`daterange`), but **cannot index standard scalar types like `uuid` or `integer` with equality (`=`) without the `btree_gist` extension**.
- **Evidence**: `apps/core/migrations/0001_enable_postgis.py` enables `postgis`, but `btree_gist` is not enabled in the repository. Attempting to add an exclusion constraint combining `property_id` (UUID) and `daterange` will immediately fail in PostgreSQL with `data type uuid has no default operator class for access method "gist"`.
- **Required Action**: `apps.core` or the initial migration of `apps.tenancy` must declare a dependency executing `CREATE EXTENSION IF NOT EXISTS btree_gist;`.

---

## 3. Review Area 2 — Occupancy Consistency & State Synchronization

### Finding 2.1: Dual Source of Truth Between Operations and Tenancy (HIGH)
- **Proposed in Spec**: `UnitOperationalProfile.occupancy_status` stores `OCCUPIED`, `VACANT_AVAILABLE`, etc., and `current_lease_id` ([M7 Spec Section 8.1](file:///home/mr-robot/Projects/glitters-core/docs/engineering/M7_PROPERTY_MANAGEMENT_ARCHITECTURE.md#L370-L377)), while `Lease.status` stores `ACTIVE`, `TERMINATED`, etc. ([M7 Spec Section 8.2](file:///home/mr-robot/Projects/glitters-core/docs/engineering/M7_PROPERTY_MANAGEMENT_ARCHITECTURE.md#L474)).
- **Failure Mode**:
  - This is an antipattern violating the Single Source of Truth (SSOT) principle.
  - If a lease expires via Celery Beat, or an eviction order terminates a lease in `apps.tenancy`, but a network error, database lock, or unhandled exception interrupts the secondary write to `UnitOperationalProfile.occupancy_status`, the database enters an unrecoverable split-brain state: `Lease` reports `TERMINATED`, while `UnitOperationalProfile` reports `OCCUPIED`.
- **DDD Separation of Concerns**:
  - **Contractual Occupancy** is an *aggregate property of Tenancy*.
  - **Physical Readiness** is an *aggregate property of Operations*.
  - They must never be merged into a single denormalized column.
  - **Authoritative Resolution**:
    1. Delete `current_lease_id` and remove `OCCUPIED` and `NOTICE_PERIOD` from `UnitOperationalProfile`.
    2. Replace `occupancy_status` with `physical_state`:
       - `CLEAN_INSPECTED`: Inspected, clean, ready for occupancy.
       - `TURNOVER_REQUIRED`: Tenant moved out; requires cleaning/inspection.
       - `REPAIR_HOLD`: Work order active; cannot occupy.
       - `MAINTENANCE_OFFLINE`: Structural/utility outage.
       - `ADMINISTRATIVE_HOLD`: Owner reserved.
    3. Calculate unit commercial availability as a pure selector expression combining physical state and active lease range:
       $$\text{Unit Available} \iff (\text{physical\_state} = \text{CLEAN\_INSPECTED}) \land \neg \exists \, \text{Lease} \text{ where } \text{status} \in \{\text{ACTIVE}, \text{PENDING\_SIGNATURE}\} \land \text{daterange} \cap \text{target\_range} \ne \emptyset$$

---

## 4. Review Area 3 — Property Management Authority & Delegation

### Finding 3.1: Authority Hijacking via Unverified `PropertyParty` Claims (BLOCKER)
- **Proposed in Spec**: `ManagementAgreement.owner_party` is a foreign key to `PropertyParty` ([M7 Spec Section 8.1](file:///home/mr-robot/Projects/glitters-core/docs/engineering/M7_PROPERTY_MANAGEMENT_ARCHITECTURE.md#L354)).
- **Codebase Invariant in `apps.properties`**:
  - In [apps/properties/models.py#L220-L250](file:///home/mr-robot/Projects/glitters-core/apps/properties/models.py#L220-L250), `PropertyParty.status` choices are `claimed`, `pending_verification`, `verified`, `rejected`, `revoked`.
  - In [apps/properties/domain.py#L35](file:///home/mr-robot/Projects/glitters-core/apps/properties/domain.py#L35), `VERIFICATION_UNAVAILABLE = "A relationship cannot be marked verified through this API."`
  - A `PropertyParty` with `role="owner"` is merely a **claim**. Anyone with an active user account can submit a claim asserting they own a property.
- **The Exploit**:
  - If a malicious property management company creates a `ManagementAgreement` referencing an unverified `claimed` party row, and the agreement transitions to `ACTIVE`, the management company acquires operational control over an innocent user's canonical property record!
- **Architectural Remedy**:
  1. A `ManagementAgreement` must **not** bind to an unverified `PropertyParty`.
  2. For personal properties: The granting party must be the user holding the direct `PropertyAccess(capability="manage")` created at property insertion.
  3. For corporate properties: The granting party must be the owner organization holding active ownership authority.
  4. **Two-Sided Agreement Handshake**: A `ManagementAgreement` cannot be activated unilaterally. If drafted by the management company, it enters `PENDING_OWNER_ACCEPTANCE` and requires a cryptographic acceptance action by the verified asset owner.

### Finding 3.2: Violation of M4 Context Immutability (HIGH)
- **Codebase Invariant in `apps.properties`**:
  - In [apps/properties/models.py#L103-L115](file:///home/mr-robot/Projects/glitters-core/apps/properties/models.py#L103-L115), database check constraint `property_management_context` enforces:
    `management_context="personal" <=> managing_organization IS NULL`.
  - In [apps/properties/domain.py#L21](file:///home/mr-robot/Projects/glitters-core/apps/properties/domain.py#L21), `CONTEXT_IMMUTABLE = "Management context cannot be changed."`
- **Specification Conflict**: The M7 specification states that an agreement sets `Property.managing_organization`. If a property was created under `management_context="personal"`, attempting to assign a `managing_organization` will crash on the database check constraint!
- **Architectural Remedy**:
  - Canonical properties created as `personal` must remain `personal`.
  - Management delegation must be modeled as a **Delegated Operational Grant**: activating a `ManagementAgreement` creates a scoped `PropertyAccess(user=..., capability="manage", origin="agreement")` for the appointed property manager, without mutating the canonical property record's immutable `management_context`.

---

## 5. Review Area 4 — Deposits and Financial Integration

### Finding 4.1: Tenancy Declaring Independent Financial Settlement (HIGH)
- **Proposed in Spec**: M7 specification defines deposit deductions and move-out refunds inside `apps.tenancy` ([M7 Spec Section 3.4](file:///home/mr-robot/Projects/glitters-core/docs/engineering/M7_PROPERTY_MANAGEMENT_ARCHITECTURE.md#L207-L213)).
- **Vulnerability**:
  - If `apps.tenancy` records a `DepositDeduction` and calculates `net_refund_payable_minor`, and the property manager marks the deposit "REFUNDED" via a tenancy endpoint, Tenancy has declared cash movement without bank verification.
  - If the landlord lacks liquidity, or if the M-Pesa B2C disbursement fails, the database asserts the deposit was refunded when the tenant has received zero money.
- **Boundary Invariant**:
  - `apps.tenancy` may only calculate **Contractual Liability Assessments**:
    - `SecurityDepositAssessment`: Obligation established at lease start.
    - `DepositDeductionAssessment`: Itemized damage claim backed by a completed work order or inspection item.
    - `RefundAuthorization`: A notice from landlord/manager authorizing the release of up to $X$ funds.
  - **Financial Settlement Rule**: Only `apps.finance` may emit an `EscrowDisbursementConfirmedEvent`. Tenancy must subscribe to this event before displaying a deposit status as `SETTLED`.

---

## 6. Review Area 5 — Contractor Access & Data Minimization

### Finding 5.1: Tenant and Owner PII Exposure to External Contractors (HIGH)
- **Proposed in Spec**: `WorkOrder` links to `MaintenanceRequest`, which directly references `reported_by` (Tenant) and `Property` (Owner) ([M7 Spec Section 8.1](file:///home/mr-robot/Projects/glitters-core/docs/engineering/M7_PROPERTY_MANAGEMENT_ARCHITECTURE.md#L400-L425)).
- **Statutory Violation**:
  - Under the **Kenya Data Protection Act, 2019** (Sections 3, 29, 30 — Principles of Data Protection, Data Minimization), disclosing a tenant's personal email, national ID, lease terms, or the property owner's personal identity to an external plumbing or electrical contractor constitutes an unlawful data leak.
- **Architectural Remedy**:
  1. Create a segregated **Contractor Dispatch View DTO**:
     ```python
     @dataclass(frozen=True)
     class ContractorWorkOrderDTO:
         work_order_id: UUID
         property_address_line: str  # e.g., "Apartment 4B, Kilimani Heights"
         trade: str
         scope_of_work: str
         scheduled_window_start: datetime
         scheduled_window_end: datetime
         on_site_contact_name: str    # First name only (e.g. "Jane")
         on_site_contact_phone: str   # Masked or operational phone
         # STRICTLY OMITTED: tenant_email, rent_amount, owner_name, owner_id
     ```
  2. **Bounded Access Expiration**: A contractor organization's read token for a work order must automatically expire 48 hours after `scheduled_end` or immediately upon status moving to `COMPLETED`.

---

## 7. Review Area 6 — Rent Schedules and Arrears Calculations

### Finding 6.1: Shadow General Ledger in `RentScheduleEntry` (HIGH)
- **Proposed in Spec**: `RentScheduleEntry` contains `status` (`PAID`, `PARTIALLY_PAID`, `OVERDUE`), `paid_amount_minor`, and `settled_at` ([M7 Spec Section 8.2](file:///home/mr-robot/Projects/glitters-core/docs/engineering/M7_PROPERTY_MANAGEMENT_ARCHITECTURE.md#L490-L505)).
- **Vulnerability**:
  - Storing `paid_amount_minor` on a schedule entry assumes payments map 1-to-1 to discrete months.
  - In actual real estate operations:
    - Tenants pay partial amounts (e.g., KES 20,000 against a KES 50,000 rent).
    - Tenants prepay 3 months in advance.
    - Tenants make lump-sum payments covering rent plus water arrears in one M-Pesa transaction.
    - Payments bounce or M-Pesa reverses transactions.
  - If Tenancy directly updates `paid_amount_minor`, it is executing payment allocation logic without a general ledger!
- **Architectural Remedy**:
  - `RentScheduleEntry` must represent **Contractual Demands Only**:
    - `id`, `lease`, `sequence_number`, `period_start`, `period_end`, `due_date`, `base_rent_minor`, `service_charge_minor`, `total_due_minor`, `status` (`SCHEDULED`, `BILLED`, `CANCELLED`).
  - Drop `paid_amount_minor` and `settled_at` from `RentScheduleEntry`.
  - When M8 (`apps.finance`) arrives, it generates `Invoice` records against `RentScheduleEntry`.
  - Payment settlement and allocation live in Finance. Tenancy calculates "Unsettled Billed Demands", never legal cash arrears.

---

## 8. Review Area 7 — Audit and Event Integrity

### Finding 7.1: Missing Event Deduplication and Transaction Atomicity (MEDIUM)
- **Proposed in Spec**: `OperationsEvent` and `TenancyEvent` using `AppendOnlyQuerySet` ([M7 Spec Section 7](file:///home/mr-robot/Projects/glitters-core/docs/engineering/M7_PROPERTY_MANAGEMENT_ARCHITECTURE.md#L272-L315)).
- **Vulnerability**:
  - In high-throughput operations (e.g., Celery Beat batch processing 10,000 rent schedules), task retries following network glitches can dispatch duplicate events (e.g. two `rent_schedule.generated` events for the same lease sequence).
- **Architectural Invariant**:
  - All event emissions must occur **strictly inside the atomic database transaction** of the mutating service method.
  - Event tables must enforce an **idempotency hash constraint**:
    ```python
    idempotency_key = models.CharField(max_length=64, unique=True, null=True, blank=True)
    ```
    Where `idempotency_key = sha256(f"{action}:{object_type}:{object_id}:{request_id}")`. Duplicate emissions silently abort without double-recording.

---

## 9. Review Area 8 — Multi-Tenancy Isolation Risks

### Finding 8.1: Dual-Tenant Isolation Leak on Contractor Work Orders (HIGH)
- **Proposed in Spec**: `WorkOrder.contractor_organization` links to an external contractor firm ([M7 Spec Section 8.1](file:///home/mr-robot/Projects/glitters-core/docs/engineering/M7_PROPERTY_MANAGEMENT_ARCHITECTURE.md#L405)).
- **The Multi-Tenancy Vulnerability**:
  - Existing `glitters-core` multi-tenancy assumes a record belongs to *one* active organization.
  - A `WorkOrder` involves **two distinct organizations**:
    1. The Managing Organization (Client).
    2. The Contractor Organization (Vendor).
  - If a standard organization queryset filter is applied (`filter(organization=caller_org)`), which organization owns the row?
  - If the filter checks `managing_organization`, the contractor cannot see their jobs.
  - If the filter checks `Q(managing_organization=org) | Q(contractor_organization=org)`, a contractor calling the generic `/api/v1/operations/work-orders/` endpoint could execute state transitions reserved for the property manager (e.g. marking the job approved or billing the owner)!
- **Architectural Remedy**:
  - Implement explicit **Bilateral State Permissions**:
    - Property Manager holds `workorders.manage` (can dispatch, approve, cancel, sign-off).
    - Contractor holds `workorders.execute` (can update status to `IN_PROGRESS`, submit completion notes, and upload repair photos).
    - The service layer must explicitly enforce that a contractor user cannot invoke manager-only transition methods.

---

## 10. Review Area 9 — Database Performance & Query Optimization

### Finding 10.1: Missing Composite Indexes on High-Frequency Operational Queues (MEDIUM)
- **Proposed in Spec**: Standard individual foreign key indexes.
- **Performance Bottleneck**:
  - The property manager dashboard executes high-volume filtered queries:
    - *Maintenance Queue*: `WHERE managing_organization_id = X AND status IN ('REPORTED', 'TRIAGED') ORDER BY priority, created_at`
    - *Arrears Aging*: `WHERE lease__managing_organization_id = X AND due_date < today AND status = 'DUE'`
    - *Inspection Schedule*: `WHERE property__managing_organization_id = X AND inspection_date >= today`
- **Required Composite Indexes**:
  1. `apps.operations.models.MaintenanceRequest`:
     `Index(fields=["property", "status", "priority"], name="maint_req_prop_status_idx")`
  2. `apps.operations.models.WorkOrder`:
     `Index(fields=["contractor_organization", "status"], name="wo_contractor_status_idx")`
  3. `apps.tenancy.models.RentScheduleEntry`:
     `Index(fields=["lease", "due_date", "status"], name="rent_sched_due_idx")`

---

## 11. Review Area 10 — API & Workspace Readiness

### Finding 11.1: Missing Granular Workspace Endpoints and Read Permissions (MEDIUM)
- **Proposed in Spec**: Endpoints in Section 9 combine views without distinguishing between manager and external participant workspaces.
- **Deficiencies Identified**:
  1. **Missing `inspections.view`**: The spec only defined `inspections.execute`. A tenant needs to inspect their move-in condition report without being granted permission to execute inspections on other units.
  2. **Missing Owner Portal Endpoints**: No endpoint allows a property owner to review and approve work orders exceeding `spend_limit_minor`.
     - *Required Endpoint*: `POST /api/v1/operations/work-orders/<id>/owner-approval/`
  3. **Missing Contractor Bidding Endpoint**: For large repairs, contractors submit estimates before dispatch.
     - *Required Endpoint*: `POST /api/v1/operations/maintenance/requests/<id>/quotes/`

---

## 12. Complete Finding Classification Matrix

| Finding Ref | Finding Title | Severity | Impact Summary | Required Action |
|---|---|---|---|---|
| **REV-01** | **Lease Overlap & Pre-Leasing Failure** | **BLOCKER** | Partial unique constraint fails on future leases and prevents pre-leasing. | Implement PostgreSQL `daterange` GiST exclusion constraint with half-open `[start, end)` semantics. |
| **REV-02** | **Management Authority Hijacking** | **BLOCKER** | Agreements bind to unverified party claims, allowing operational hijacking. | Enforce two-sided cryptographic agreement handshake requiring verified owner sign-off. |
| **REV-03** | **Split-Brain Occupancy State** | **HIGH** | Denormalized occupancy status duplicates lease state, risking desynchronization. | Remove `current_lease_id`; contractual occupancy is derived from `Lease`; unit stores `physical_state` only. |
| **REV-04** | **Missing `btree_gist` Extension** | **HIGH** | GiST exclusion constraints on UUID + daterange fail without `btree_gist`. | Create migration enabling `btree_gist` in PostgreSQL. |
| **REV-05** | **Shadow General Ledger in Tenancy** | **HIGH** | Storing `paid_amount_minor` on rent schedules violates financial boundaries. | Remove payment fields from schedules; Tenancy tracks billing demands only. |
| **REV-06** | **Contractor Tenant PII Leak** | **HIGH** | Work order dispatch exposes tenant email and owner identity to contractors. | Implement segregated, data-minimized Contractor Dispatch DTO with bounded token lifetime. |
| **REV-07** | **Bilateral Multi-Tenancy in Work Orders** | **HIGH** | Shared records between management and contractor orgs risk permission leaks. | Enforce distinct service-level permissions for Client Orgs vs Vendor Orgs. |
| **REV-08** | **M4 Context Immutability Conflict** | **HIGH** | Agreement assigning managing org violates personal property check constraint. | Model management delegation as an operational `PropertyAccess` grant without altering context. |
| **REV-09** | **Missing Composite Queue Indexes** | **MEDIUM** | Maintenance and rent schedule triage queries will table-scan at scale. | Add composite indexes on `(org, status, created_at)` and `(lease, due_date)`. |
| **REV-10** | **Event Deduplication & Atomicity** | **MEDIUM** | Celery retries can emit duplicate append-only audit events. | Add unique idempotency hash constraint on audit events inside atomic service transactions. |
| **REV-11** | **Missing Participant Workspace Endpoints** | **MEDIUM** | Tenant inspection read and owner expense approvals lack dedicated routes. | Add `inspections.view`, owner approval endpoint, and contractor quotation routes. |

---

## 13. Review Conclusions & Actionable Directives

### 13.1 Approved Architectural Decisions
1. **Separation of Physical Asset from Operations**: Keeping canonical properties in `apps.properties` and operational workflows in `apps.operations` and `apps.tenancy` is fully approved.
2. **Single Account Model (`identity.User`)**: Enforcing a unified user model across owners, managers, contractors, and tenants is fully approved.
3. **Integer Minor-Unit Currency**: Storing all financial values as `BigIntegerField` cents is fully approved.
4. **Append-Only Audit Philosophy**: Requiring `AppendOnlyQuerySet` on operations and tenancy events is fully approved.

### 13.2 Required Specification Corrections (Must Update M7 Spec)
1. **Adopt Half-Open Interval Date Semantics**: Replace date equality checks with `[start_date, effective_end_date)` date ranges.
2. **Eliminate Denormalized Occupancy Columns**: Replace `UnitOperationalProfile.occupancy_status` with `physical_state` (`CLEAN_INSPECTED`, `TURNOVER_REQUIRED`, `REPAIR_HOLD`, `MAINTENANCE_OFFLINE`, `ADMINISTRATIVE_HOLD`).
3. **Strip Payment Fields from Tenancy**: Remove `paid_amount_minor` and `status="PAID"` from `RentScheduleEntry`. Tenancy manages billing demands; settlement occurs via integration events from Finance.
4. **Redesign `ManagementAgreement` Handshake**: Require explicit owner acceptance before any agreement can establish operational authority.

### 13.3 Mandatory Database Constraints for M7
```sql
-- 1. Enable btree_gist extension
CREATE EXTENSION IF NOT EXISTS btree_gist;

-- 2. Non-overlapping lease exclusion constraint
ALTER TABLE tenancy_lease
ADD CONSTRAINT exclude_overlapping_active_leases
EXCLUDE USING gist (
    property_id WITH =,
    daterange(start_date, effective_end_date, '[)') WITH &&
)
WHERE (status IN ('ACTIVE', 'PENDING_SIGNATURE', 'UNDER_RENEWAL'));

-- 3. Agreement date range validity
ALTER TABLE operations_management_agreement
ADD CONSTRAINT check_agreement_dates
CHECK (valid_until IS NULL OR valid_until >= valid_from);

-- 4. Work order non-negative costs
ALTER TABLE operations_work_order
ADD CONSTRAINT check_work_order_costs
CHECK (estimated_cost_minor >= 0 AND (actual_cost_minor IS NULL OR actual_cost_minor >= 0));
```

### 13.4 M7-A Implementation Acceptance Criteria
Before sub-milestone M7-A code is authored, the following acceptance criteria must be satisfied:
1. Migration enabling `btree_gist` prepared in `apps.core`.
2. Agreement service requires verified owner identity before delegation.
3. Operational unit profile models physical readiness independently of contractual occupancy.
4. 100% of existing 276 tests continue to pass without regression.

### 13.5 Risks Safely Deferred to M8 (Finance)
1. Double-entry general ledger posting.
2. M-Pesa STK push and C2B/B2C automated transaction clearing.
3. Bank statement reconciliation algorithms.
4. Withholding tax deduction and KRA electronic fiscal invoice (eTIMS) synchronization.

---
*End of Independent Architectural Review.*
