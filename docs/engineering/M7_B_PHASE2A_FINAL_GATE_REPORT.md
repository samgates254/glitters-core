# GLITTERS CORE — Milestone M7-B Phase 2A Final Gate Review Report

**Document Path**: `docs/engineering/M7_B_PHASE2A_FINAL_GATE_REPORT.md`
**Milestone**: Milestone 7-B (Tenancy Domain & Temporal Occupancy Engine — Phase 2A Final Integrity Review)
**Status**: CONDITIONAL PASS — APPROVED FOR PHASE 2B UPON RESOLUTION OF TWO NARROW DEFECTS
**Date**: November 12, 2026
**Auditor**: Principal Software Architect & Adversarial Verification Engineer
**Repository**: `glitters-core` ([/home/mr-robot/Projects/glitters-core](file:///home/mr-robot/Projects/glitters-core))
**Companion Documents**:
- [M7_B_APPROVED_ARCHITECTURE.md](file:///home/mr-robot/Projects/glitters-core/docs/engineering/M7_B_APPROVED_ARCHITECTURE.md)
- [M7_B_PHASE2A_REMEDIATION_REPORT.md](file:///home/mr-robot/Projects/glitters-core/docs/engineering/M7_B_PHASE2A_REMEDIATION_REPORT.md)
- [M7_B_PHASE1_FINAL_VERIFICATION.md](file:///home/mr-robot/Projects/glitters-core/docs/engineering/M7_B_PHASE1_FINAL_VERIFICATION.md)

---

## 1. Executive Summary & Verdict

### Final Gate Verdict: **CONDITIONAL PASS**

The Phase 2A integrity remediation was subjected to source-level architectural inspection and targeted constraint evaluation across three foundational gates:
1. **Gate 1 — Management Authority Revocation**: Identified a **Defect (Medium Severity)** where `revoke_lease_execution_authority()` and `terminate_agreement()` mutate `can_execute_leases = False` in place on `ManagementAgreement`, destructively overwriting the original contractual grant.
2. **Gate 2 — Rentable-Unit Validation Enforcement**: Identified an **Enforcement Boundary Gap (Low-Medium Severity)** where `Lease.clean()` invokes `validate_rentable_unit()`, but `Lease.save()` does not call `clean()`, leaving direct ORM `Lease.objects.create()` unprotected.
3. **Gate 3 — Early Termination and Signed-Contract Preservation**: Confirmed that `term_end_date` is immutable and preserved permanently, but identified a **Specification Ambiguity (Medium Severity)** in `contract_data_hash` regarding `effective_end_date` vs. `term_end_date`, and documented the interaction between `TERMINATED_EARLY` and the partial GiST exclusion constraint.

Phase 2B implementation is **authorized to proceed**, conditioned upon incorporating the recommended narrow corrections at the initiation of Phase 2B.

---

## 2. Gate-by-Gate Architectural Analysis

### Gate 1: Management Authority Revocation

#### 1.1 Source Inspection
- **Model**: [`apps/operations/models.py#L45-L85`](file:///home/mr-robot/Projects/glitters-core/apps/operations/models.py#L45-L85) (`ManagementAgreement.can_execute_leases`)
- **Revocation Service**: [`apps/operations/services/agreements.py#L865-L868`](file:///home/mr-robot/Projects/glitters-core/apps/operations/services/agreements.py#L865-L868) (`revoke_lease_execution_authority`)
- **Termination Service**: [`apps/operations/services/agreements.py#L777-L780`](file:///home/mr-robot/Projects/glitters-core/apps/operations/services/agreements.py#L777-L780) (`terminate_agreement`)
- **Model Save Guard**: [`apps/operations/models.py#L154-L175`](file:///home/mr-robot/Projects/glitters-core/apps/operations/models.py#L154-L175) (`_allow_authority_revocation`)
- **Authority Verification**: [`apps/operations/services/agreements.py#L900-L938`](file:///home/mr-robot/Projects/glitters-core/apps/operations/services/agreements.py#L900-L938) (`verify_lease_execution_authority`)

#### 1.2 Evaluation Against the Seven Invariants

| # | Invariant | Current Status | Finding & Source Evidence |
| :--- | :--- | :---: | :--- |
| **1** | **Originally accepted authority grant remains historically intact** | **FAIL (Defect)** | [`apps/operations/services/agreements.py#L866`](file:///home/mr-robot/Projects/glitters-core/apps/operations/services/agreements.py#L866) explicitly mutates `agreement.can_execute_leases = False` on the row. Once saved, the historical fact that the agreement originally contained lease execution power is destroyed on the agreement table. |
| **2** | **Revocation changes current authority effectiveness** | **PASS** | `verify_lease_execution_authority()` checks `can_execute_leases`, immediately denying access post-revocation. |
| **3** | **System retains who revoked the authority, when, and why** | **PARTIAL** | Retained in `OperationsEvent` audit metadata ([`apps/operations/services/agreements.py#L870-L882`](file:///home/mr-robot/Projects/glitters-core/apps/operations/services/agreements.py#L870-L882)), but missing from first-class columns on `ManagementAgreement`. |
| **4** | **Revocation must not silently rewrite the accepted agreement** | **FAIL (Defect)** | Directly mutating `can_execute_leases` rewrites the accepted bilateral agreement row. The save guard was relaxed via `_allow_authority_revocation = True` to permit this rewrite. |
| **5** | **Agreement termination preserves original contractual grant while ending effectiveness** | **FAIL (Defect)** | [`apps/operations/services/agreements.py#L777`](file:///home/mr-robot/Projects/glitters-core/apps/operations/services/agreements.py#L777) also sets `can_execute_leases = False` during termination, conflating agreement termination with contractual terms erasure. |
| **6** | **Authorization checks immediately reject revoked authority** | **PASS** | Verified in `test_owner_explicit_revocation_of_lease_execution_authority` and `test_agreement_termination_revokes_lease_execution_authority`. |
| **7** | **Historical agreement and audit records remain reconstructable** | **PARTIAL** | Reconstructable only by traversing unstructured JSON event logs; unreconstructable from `ManagementAgreement` state alone. |

#### 1.3 Recommended Correction for Gate 1
Separate the **immutable contractual term** from the **revocation lifecycle state**:
1. Keep `can_execute_leases = models.BooleanField(default=False)` permanently immutable on `ManagementAgreement` once accepted. Remove `_allow_authority_revocation`.
2. Add explicit revocation lifecycle columns to `ManagementAgreement`:
   - `lease_execution_revoked_at = models.DateTimeField(null=True, blank=True)`
   - `lease_execution_revoked_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")`
   - `lease_execution_revocation_reason = models.TextField(blank=True, default="")`
3. Add property on `ManagementAgreement`:
   ```python
   @property
   def has_effective_lease_execution_authority(self) -> bool:
       today = timezone.now().date()
       return (
           self.can_execute_leases
           and self.status == AgreementStatus.ACTIVE
           and self.lease_execution_revoked_at is None
           and (self.valid_until is None or self.valid_until >= today)
       )
   ```
4. Update `revoke_lease_execution_authority()`:
   Set `agreement.lease_execution_revoked_at = now`, `agreement.lease_execution_revoked_by = actor`, `agreement.lease_execution_revocation_reason = reason`. Do **not** touch `can_execute_leases`.
5. Update `terminate_agreement()`:
   Set `agreement.status = AgreementStatus.TERMINATED`. Do **not** touch `can_execute_leases`.

---

### Gate 2: Rentable-Unit Validation Enforcement

#### 2.1 Source Inspection
- **Validation Service**: [`apps/tenancy/services/validation.py#L12-L68`](file:///home/mr-robot/Projects/glitters-core/apps/tenancy/services/validation.py#L12-L68) (`validate_rentable_unit`)
- **Lease Model Validation**: [`apps/tenancy/models.py#L407-L411`](file:///home/mr-robot/Projects/glitters-core/apps/tenancy/models.py#L407-L411) (`Lease.clean`)
- **Lease Save Implementation**: [`apps/tenancy/models.py#L413-L435`](file:///home/mr-robot/Projects/glitters-core/apps/tenancy/models.py#L413-L435) (`Lease.save`)

#### 2.2 Answers to Inspection Questions

1. **Does `Lease.clean()` call the validator?**
   - **YES**. Lines 407-411 in `apps/tenancy/models.py` import and invoke `validate_rentable_unit(self.property)`.
2. **Does `Lease.save()` call `full_clean()`?**
   - **NO**. `Lease.save()` performs immutability checks on existing instances (`if not self._state.adding`), but does **not** invoke `self.clean()` or `self.full_clean()`.
3. **Do any services, admin paths, tests, or other code create Lease records without validation?**
   - **YES**. Standard Django ORM write paths (e.g., `Lease.objects.create(...)` and `lease.save()`) do not call `clean()` unless explicitly called by the caller. Direct ORM creation in scripts, background jobs, or direct test fixtures bypasses `clean()`.
4. **Can an aggregate property be persisted as a draft Lease by bypassing `clean()`?**
   - **YES**. Calling `Lease.objects.create(property=building, status=LeaseStatus.DRAFT, ...)` succeeds and persists a draft lease on an aggregate building because neither Django ORM nor PostgreSQL check constraints block foreign keys to buildings.
5. **Is there a database-level invariant where one is feasible?**
   - **Database Check Constraint**: NOT FEASIBLE. PostgreSQL check constraints do not support cross-table subqueries (`CHECK ((SELECT hierarchy_role FROM properties_property WHERE id = property_id) IN ('unit', 'standalone'))` raises `psycopg.errors.FeatureNotSupported: cannot use subquery in check constraint`).
   - **Database Trigger**: FEASIBLE in raw PostgreSQL, but contrary to `glitters-core` convention where business domain logic lives in Python domain services.

#### 2.3 Recommended Enforcement Boundary for Phase 2B
1. **Model-Level Guard in `Lease.save()`**:
   Add `clean()` invocation during record insertion:
   ```python
   def save(self, *args, **kwargs):
       if self._state.adding and self.property_id:
           from apps.tenancy.services.validation import validate_rentable_unit
           validate_rentable_unit(self.property)
       ...
       super().save(*args, **kwargs)
   ```
   This ensures that even direct `Lease.objects.create()` or `lease.save()` calls will trigger `validate_rentable_unit()`.
2. **Service-Level Mandate in `create_draft_lease()`**:
   Phase 2B's `create_draft_lease(*, actor, property_obj, ...)` must execute `validate_rentable_unit(property_obj)` as its very first operational precondition prior to any model allocation.

---

### Gate 3: Early Termination and Signed-Contract Preservation

#### 3.1 Source Inspection
- **Immutable Fields Definition**: [`apps/tenancy/models.py#L127-L146`](file:///home/mr-robot/Projects/glitters-core/apps/tenancy/models.py#L127-L146) (`IMMUTABLE_CONTRACT_FIELDS`)
- **Date Columns on `Lease`**: [`apps/tenancy/models.py#L247-L270`](file:///home/mr-robot/Projects/glitters-core/apps/tenancy/models.py#L247-L270) (`start_date`, `term_end_date`, `effective_end_date`, `actual_termination_date`, `actual_handover_date`)
- **GiST Exclusion Constraint**: [`apps/tenancy/models.py#L389-L399`](file:///home/mr-robot/Projects/glitters-core/apps/tenancy/models.py#L389-L399) (`tenancy_lease_exclude_overlapping_contractual_leases`)
- **Status Definition**: [`apps/tenancy/domain.py#L107-L135`](file:///home/mr-robot/Projects/glitters-core/apps/tenancy/domain.py#L107-L135) (`LeaseStatus`, `OCCUPANCY_RESERVING`, `EXECUTED`)

#### 3.2 Evaluation of Contract Preservation & Date Semantics

1. **Whether `effective_end_date` is included in the signed canonical contract payload**:
   - In [`docs/engineering/M7_B_APPROVED_ARCHITECTURE.md#L196`](file:///home/mr-robot/Projects/glitters-core/docs/engineering/M7_B_APPROVED_ARCHITECTURE.md#L196), the specification narrative mentioned `effective_end_date` alongside `start_date` and `term_end_date` as part of the structured hash.
   - **Defect/Risk**: Including `effective_end_date` in `contract_data_hash` conflates contractual terms with PostgreSQL GiST engine indexing. In legal contracts, signatories agree to `start_date` (commencement) and `term_end_date` (expiry). `effective_end_date` is strictly an internal database representation (`term_end_date + 1 day` for half-open intervals).
   - If `effective_end_date` were adjusted upon early termination, and it was part of `contract_data_hash`, the hash would no longer verify against the executed contract.
2. **Whether changing it invalidates or contradicts the original contract hash**:
   - In [`apps/tenancy/models.py#L127-L146`](file:///home/mr-robot/Projects/glitters-core/apps/tenancy/models.py#L127-L146), `term_end_date` **is** in `IMMUTABLE_CONTRACT_FIELDS`, while `effective_end_date` was excluded from `IMMUTABLE_CONTRACT_FIELDS`.
   - Therefore, mutating `effective_end_date` does not raise an ORM validation error, and because `contract_data_hash` in Phase 2B should strictly hash `start_date` and `term_end_date`, the signed hash remains valid and unmodified.
3. **Whether the original executed contractual term can be reconstructed**:
   - **YES**. `start_date`, `term_end_date`, `rent_amount_minor`, and the cryptographic hashes are strictly immutable once executed. An auditor can always determine the exact signed terms regardless of early termination.
4. **Whether termination is represented as a separate legal event or amendment**:
   - Early termination is represented as an append-only `TenancyEvent` (action `lease.terminated_early`) and updates lifecycle status to `LeaseStatus.TERMINATED_EARLY`. It is not an amendment; amendments are for continuing leases with modified terms.
5. **Whether the GiST exclusion constraint releases future dates correctly**:
   - **Critical Architectural Insight**:
     The GiST exclusion constraint has the condition:
     ```sql
     WHERE ("status" IN ('pending_signature', 'fully_executed', 'active', 'under_renewal'))
     ```
     When a lease is early-terminated, its `status` changes to `TERMINATED_EARLY`.
     Because `TERMINATED_EARLY` is **not** in the partial index condition, PostgreSQL **completely removes the terminated lease from the GiST index**.
     Thus, changing `effective_end_date` is not even what releases future dates—the status transition to `TERMINATED_EARLY` is what releases the property in GiST!
     **Consequence**: Once a lease is in `TERMINATED_EARLY`, GiST no longer indexes the historical period `[start_date, actual_termination_date)` either. (Historical backdated collision protection must be enforced in service logic or by indexing terminated leases up to `actual_termination_date`).
6. **Whether historical occupancy and legal termination dates remain distinguishable**:
   - **YES**. Five separate, non-overlapping columns exist on `Lease`:
     1. `start_date`: Contractual commencement date.
     2. `term_end_date`: Contractual stated expiry (immutable).
     3. `actual_termination_date`: Legal early termination date.
     4. `actual_handover_date`: Physical keys surrender date.
     5. `effective_end_date`: Upper boundary for GiST indexing.

---

## 3. Findings and Severity Matrix

| Finding ID | Gate | Severity | Finding Summary | Recommended Action in Phase 2B |
| :--- | :---: | :---: | :--- | :--- |
| **F-GATE-01** | **Gate 1** | **Medium** | Destructive overwrite of accepted contractual term `can_execute_leases` during revocation and termination. | Add `lease_execution_revoked_at`, `revoked_by`, `revocation_reason` columns; make `can_execute_leases` permanently immutable on agreement. |
| **F-GATE-02** | **Gate 2** | **Low-Medium** | Direct ORM `Lease.objects.create()` bypasses `Lease.clean()` and can persist leases on aggregate properties. | Add `if self._state.adding: self.clean()` to `Lease.save()`; enforce upfront validation in Phase 2B `create_draft_lease()`. |
| **F-GATE-03** | **Gate 3** | **Medium** | Specification inconsistency regarding whether `effective_end_date` is in `contract_data_hash`. | Clarify that `contract_data_hash` hashes strictly contractual terms (`start_date`, `term_end_date`), excluding internal engine dates. |
| **F-GATE-04** | **Gate 3** | **Low** | GiST constraint condition excludes `TERMINATED_EARLY`, unindexing both past and future periods. | Document that GiST protects active/pending reservations; historical overlap protection for terminated leases is enforced by service logic. |

---

## 4. Supporting Test Evidence

The following targeted integration tests from [`tests/integration/test_m7b_phase2a_remediation.py`](file:///home/mr-robot/Projects/glitters-core/tests/integration/test_m7b_phase2a_remediation.py) demonstrate current behavior:

| Test Name | Verified Behavior | Result |
| :--- | :--- | :---: |
| `test_mandated_managing_agent_possesses_lease_execution_authority` | Mandated agent (`can_execute_leases=True`) possesses signing authority. | **PASSED** |
| `test_ordinary_agent_without_mandate_lacks_lease_execution_authority` | Ordinary agent (`can_execute_leases=False`) rejected with 403. | **PASSED** |
| `test_unauthorized_mutation_of_can_execute_leases_is_blocked` | Mutation blocked via standard save and QuerySet update. | **PASSED** |
| `test_owner_explicit_revocation_of_lease_execution_authority` | Owner revokes authority; management access preserved; agent blocked. | **PASSED** |
| `test_canonical_rentable_unit_validation_paths` | Aggregate building, estate, subdivided, orphan units rejected. | **PASSED** |
| `test_executed_lease_immutability_guards` | Contractual fields locked on save, update, bulk_update, delete. | **PASSED** |
| `test_early_termination_preserves_contractual_history` | `term_end_date` and `start_date` remain intact during early termination. | **PASSED** |
| `test_signature_evidence_unique_party_version_constraint` | PostgreSQL `UniqueConstraint` blocks duplicate signatures. | **PASSED** |
| `test_authorized_occupant_cannot_sign_evidence` | PostgreSQL `CheckConstraint` blocks authorized occupants. | **PASSED** |

**Full Suite Baseline**: 337 passed in 69.20s.

---

## 5. Explicit Phase 2B Readiness Decision

### Decision: **CONDITIONAL PASS — AUTHORIZED FOR PHASE 2B**

Phase 2B is approved to proceed, with the following mandatory preconditions to be resolved as the very first task in Phase 2B:

1. **Non-Destructive Agreement Authority Schema**:
   Add migration to add `lease_execution_revoked_at`, `lease_execution_revoked_by`, `lease_execution_revocation_reason` to `ManagementAgreement`, and refactor `revoke_lease_execution_authority` to populate these fields while keeping `can_execute_leases = True` intact.
2. **Model-Level Insert Clean**:
   Add `if self._state.adding: self.clean()` to `Lease.save()` to guarantee that direct ORM writes cannot persist leases on aggregate properties.
3. **Canonical Contract Payload Specification**:
   Formally lock the RFC 8785 contract hashing payload to include `start_date` and `term_end_date`, explicitly excluding `effective_end_date`.

---

## 6. Stop Condition

In strict accordance with instructions:
- No code changes have been applied.
- No migrations have been created or applied.
- No Git commits have been made.
- Implementation of Phase 2B domain services and state machines remains halted pending explicit authorization.
