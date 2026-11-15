# GLITTERS CORE — Milestone M7-B Architecture Challenge & Adversarial Review

**Document Path**: `docs/engineering/M7_B_ARCHITECTURE_CHALLENGE.md`
**Milestone**: Milestone 7-B (Tenancy Domain & Temporal Occupancy Engine)
**Status**: RECONCILED ADVERSARIAL AUDIT (FINAL CONSISTENCY CERTIFICATION)
**Date**: November 12, 2026
**Author**: Independent Senior Database Architect & Application Security Auditor
**Repository**: `glitters-core` ([/home/mr-robot/Projects/glitters-core](file:///home/mr-robot/Projects/glitters-core))
**Companion Documents**:
- [M7_B_APPROVED_ARCHITECTURE.md](file:///home/mr-robot/Projects/glitters-core/docs/engineering/M7_B_APPROVED_ARCHITECTURE.md)
- [M7_B_IMPLEMENTATION_PLAN.md](file:///home/mr-robot/Projects/glitters-core/docs/engineering/M7_B_IMPLEMENTATION_PLAN.md)

---

## 1. Executive Summary & Audit Matrix

This document records the adversarial review, technical resolution, and consistency certification of all architectural challenges for Milestone M7-B. All eleven findings are resolved with formal specifications and database-level protections.

### Comprehensive Findings & Resolution Matrix

| Finding ID | Area | Initial Adversarial Concern | Reconciled Resolution | Status |
| :--- | :--- | :--- | :--- | :---: |
| **F-01** | **Authority Distinction** | Contradiction between narrative and matrix regarding standard agents and `leases.approve`; risk of unauthorized lease execution. | Clarified: standard managing agents with `leases.approve` **can** approve terms to dispatch offers. However, executing on owner behalf strictly requires `ManagementAgreement.can_execute_leases == True` AND `leases.execute`. Owner direct signature required otherwise. | **RESOLVED** |
| **F-02** | **Physical Taxonomy** | Incomplete physical occupancy states; normal tenant occupancy was absent from `UnitPhysicalProfile`. | Expanded `PhysicalState` to 7 states: `READY_INSPECTED`, `OCCUPIED_NORMAL`, `OCCUPIED_HOLDOVER`, `TURNOVER_PREPARATION`, `REPAIR_HOLD`, `STRUCTURAL_OFFLINE`, `OWNER_RESERVED`. | **RESOLVED** |
| **F-03** | **Reservation Races** | Unsigned proposals past deadline could block new reservations; deadlock risk in multi-row expiration. | Implemented transactional pre-reservation scan with explicit `ORDER BY id ASC` under `select_for_update()`. Deadline boundary formalized as $[approved\_at, deadline)$. GiST constraint acts as final kernel safeguard. | **RESOLVED** |
| **F-04** | **Dual Evidence** | Single hash failed to bind the machine-readable database payload to the human-readable visual document seen by the signatory. | Implemented dual immutable evidence values: `contract_data_hash` (RFC 8785 canonical JSON) and `rendered_document_hash` (exact visual PDF/A artifact bytes). | **RESOLVED** |
| **F-05** | **Renewal Ambiguity** | Ambiguity between renewal negotiation, amendment, and replacement leases risked GiST collision. | Placed `UNDER_RENEWAL` on incumbent lease. Drafted replacement lease covers consecutive $[T_1, T_2)$ period. Proven zero overlap in PostgreSQL GiST. | **RESOLVED** |
| **F-06** | **Pre-Leasing Lock** | Requiring unit readiness to sign contracts prevented future-dated leasing. | Decoupled **Contractual Execution** (`FULLY_EXECUTED`) from **Physical Possession** (`HandoverProtocol`). Pre-leasing is fully supported. | **RESOLVED** |
| **F-07** | **Final Day Exclusion** | Half-open intervals $[start, end)$ risked excluding the last contractual day of occupancy. | Specified `effective_end_date = term_end_date + 1 day` for fixed terms, ensuring entire `term_end_date` is included. Same-day turnover cleanly supported on boundary. | **RESOLVED** |
| **F-08** | **Multi-Signatory** | Single `tenant_user` field prevented co-tenants and guarantors from having legal standing. | Implemented `LeaseParty` model referencing canonical `identity.User` records with distinct roles and signature authority. | **RESOLVED** |
| **F-09** | **Month-End Drift** | Fixed due days (e.g. 31st) caused calendar drift or crashes in February and 30-day months. | Enforced `CheckConstraint(payment_due_day >= 1 AND payment_due_day <= 28)`. | **RESOLVED** |
| **F-10** | **Deposit Deadlock** | Unresolved damage claims deadlocked entire security deposit return. | Undisputed deposit amounts released immediately; disputed deduction items escrowed under `DISPUTED` status pending arbitration. | **RESOLVED** |
| **F-11** | **Agency Separation** | `operations.manage` could allow managing agents to execute binding contracts without explicit legal agency from the owner. | Added `can_execute_leases` flag to `ManagementAgreement`. Agents without this covenant cannot execute leases on owner behalf. | **RESOLVED** |

---

## 2. In-Depth Technical Analysis of Final Consistency Reconciliations

### 2.1 F-01: Reconciled Lease Authority Matrix & Signing Mandates
- **The Ambiguity**: In previous iterations, the matrix showed `leases.approve` as "Denied" for standard agents, while the text asserted that agents could prepare and approve terms.
- **The Resolution**:
  - Approving terms (`leases.approve`) merely creates the contractual offer, locks the terms against casual edits, acquires the temporary GiST reservation slot, and opens the tenant signature window. This is an operational workflow capability appropriately granted to property managers.
  - Executing the contract (`leases.execute`), however, binds the Property Owner to multi-year legal covenants.
  - A standard managing agent holding `leases.approve` **can** approve terms.
  - When the tenant signs, the countersignature requires legally sufficient agency:
    - If `ManagementAgreement.can_execute_leases == True`: The managing agent executes on the owner's behalf.
    - If `ManagementAgreement.can_execute_leases == False`: The agent cannot execute; the platform issues a countersigning notification to the **Property Owner** directly.

### 2.2 F-02: Complete Physical Taxonomy & Zero Contractual Duplication
- **The Gap**: In M7-A, `UnitPhysicalProfile` tracked turnover and inspection, but had no state to represent a tenant physically living in the unit, creating an operational void between Move-In and Move-Out.
- **The Resolution**:
  - Introduced `OCCUPIED_NORMAL` (normal tenant possession following Move-In handover) and `OCCUPIED_HOLDOVER` (unlawful overstay post-contractual termination).
  - All 7 physical states describe purely physical conditions. Zero contractual dates or tenant names are stored in `UnitPhysicalProfile`.
  - Prerequisite migration: `apps/operations/migrations/0002_add_physical_occupancy_states.py`.

### 2.3 F-03: Deadlock-Free Reservation Concurrency & GiST Safeguard
- **The Vulnerability**: Concurrent lease proposals competing for the same unit could cause multi-row deadlock if rows were locked in arbitrary order during stale reservation cleanup.
- **The Resolution**:
  - Stale reservations are queried with `ORDER BY id ASC` under `select_for_update()`, eliminating cyclic deadlock conditions across PostgreSQL backends.
  - Exact boundary logic: If `timezone.now() >= lease.signature_deadline`, the proposal is expired.
  - The PostgreSQL GiST exclusion constraint on `(property_id WITH =, daterange(...) WITH &&)` covers active, pending, renewal, and terminated statuses (`TERMINATED_NORMAL`, `TERMINATED_EARLY`). When a lease is terminated early, `effective_end_date` is truncated to `actual_termination_date + 1 day`: the historical occupancy window $[start\_date, effective\_end\_date)$ remains permanently immune to backdated collisions, while all future calendar days are immediately freed for subsequent tenancies.

### 2.4 F-04: Dual-Evidence Cryptographic Binding
- **The Vulnerability**: Hashing only structured JSON data left visual layout tampering undetected; hashing only PDF bytes left machine-readable querying unverified. Furthermore, including database engine dates (like `effective_end_date`) in the contractual hash would cause the cryptographic signature to break when a lease is terminated early.
- **The Resolution**:
  - `contract_data_hash`: SHA-256 of RFC 8785 canonical structured JSON, covering agreed contractual terms (`start_date`, `term_end_date`, `payment_due_day`, financial terms, parties), strictly omitting internal engine boundary `effective_end_date`. The signed hash remains permanently valid and immutable post-termination.
  - `rendered_document_hash`: SHA-256 of the exact PDF/A bytes displayed to the user.
  - Both hashes are stored on the immutable `LeaseSignatureEvidence` record. Any discrepancy between structured data and rendered visual document voids the signature.

---

## 3. Residual Operational Risks (Non-Blockers)

| Risk ID | Area | Severity | Mitigation Strategy |
| :--- | :--- | :---: | :--- |
| **R-01** | **Direct DB Superuser Raw SQL** | Low | Direct raw SQL queries by postgres superusers bypass ORM append-only rules. Standard platform behavior; PostgreSQL triggers can be applied in production if needed. |
| **R-02** | **Commercial Master Leases** | Low | Leases are restricted to `hierarchy_role in [UNIT, STANDALONE]`. Whole-building master leasing will be addressed in a future commercial milestone. |
| **R-03** | **Statutory Eviction Procedures** | Medium | Software blocks incoming move-in handover while physical holdover persists. Real-world eviction follows statutory Kenyan rent tribunal procedures. |

---

## 4. Final Implementation Readiness Verdict

All four consistency issues have been reconciled, verified against PostgreSQL 16 and Django ORM architectural standards, and documented.

### Final Verdict: **APPROVED FOR IMPLEMENTATION**
