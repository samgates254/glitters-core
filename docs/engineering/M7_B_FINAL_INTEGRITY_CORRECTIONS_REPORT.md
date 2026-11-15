# M7-B Final Integrity Corrections Report
## Tenancy Domain & Temporal Lease Engine Certification

**Milestone:** M7-B (Phase 2A Final Gate Remediation)
**Date:** November 12, 2026
**Status:** **CERTIFIED & PASS — READY FOR PHASE 2B AUTHORIZATION**
**Repository:** `glitters-core`
**Git Commit Policy:** ZERO COMMITS (Working tree preserved strictly)

---

## Executive Summary

Following the M7-B Phase 2A Final Gate Review, four architectural integrity requirements were identified to formally certify the domain models and database constraints before authorizing Phase 2B (Domain Services & Temporal State Machine):
1. **Contractual Authority History Preservation**: Retaining `ManagementAgreement.can_execute_leases` as an immutable record of the originally accepted grant while establishing dedicated revocation audit columns (`lease_execution_revoked_at`, `lease_execution_revoked_by`, `lease_execution_revocation_reason`).
2. **Rentable-Unit Persistence Protection**: Guaranteeing that non-rentable properties (aggregate buildings, estates, subdivided parents, archived properties, orphan units) cannot be referenced by a `Lease` across any ORM creation path (`save()`, `objects.create()`, `bulk_create()`).
3. **Canonical Contractual Hash Specification**: Formally specifying that `contract_data_hash` hashes agreed contractual terms (`start_date`, `term_end_date`, `payment_due_day`), strictly excluding internal engine boundaries (`effective_end_date`), ensuring cryptographic signatures remain permanently valid post-termination.
4. **Early Termination & Temporal Exclusion Integrity**: Expanding PostgreSQL GiST exclusion indexing to include `TERMINATED_NORMAL` and `TERMINATED_EARLY`, preserving historical occupancy ranges against backdated collisions while releasing future dates upon early termination.

All four corrections have been implemented, migrated, verified via PostgreSQL 16 DDL inspection, documented across architecture specifications, and certified by automated regression test suites.

---

## 1. Correction 1 — Contractual Authority History Preservation

### 1.1 Invariant Problem & Resolution
In the initial remediation, revoking lease execution authority mutated `agreement.can_execute_leases = False`. While this prevented ongoing lease execution, it destroyed the historical contractual fact that the owner had granted lease execution authority in the signed bilateral agreement.

**Resolution:**
- `ManagementAgreement.can_execute_leases` remains permanently immutable once accepted/active, preserving the historical record of the accepted legal power.
- Added three dedicated revocation audit columns to `ManagementAgreement`:
  - `lease_execution_revoked_at` (`DateTimeField`, nullable)
  - `lease_execution_revoked_by` (`ForeignKey` to `identity.User`, nullable)
  - `lease_execution_revocation_reason` (`TextField`, default `""`)
- Implemented `@builtins.property def has_effective_lease_execution_authority(self) -> bool`:
  Evaluates `True` strictly when:
  1. `can_execute_leases is True`
  2. `status == AgreementStatus.ACTIVE`
  3. `lease_execution_revoked_at is None`
  4. Current date satisfies `start_date <= today <= (valid_until or today)`
- Invariant enforcement guards in `ManagementAgreement.save()` and `ManagementAgreementQuerySet.update()` prevent:
  - Modifying `can_execute_leases` after agreement acceptance.
  - Restoring revoked authority by clearing `lease_execution_revoked_at`.
- Service refactoring:
  - `revoke_lease_execution_authority()`: Populates `lease_execution_revoked_at = timezone.now()`, `revoked_by`, and `revocation_reason` without mutating `can_execute_leases`.
  - `terminate_agreement()`: Transitions `status = TERMINATED` without mutating `can_execute_leases`.
  - `verify_lease_execution_authority()`: Asserts `agreement.has_effective_lease_execution_authority`.
- Admin & API:
  - `ManagementAgreementAdmin.readonly_fields` includes all three revocation audit fields.
  - `ManagementAgreementSerializer` exposes revocation fields and dynamic `has_effective_lease_execution_authority` boolean.

### 1.2 Migration: `operations.0003_add_lease_execution_revocation_evidence`
```sql
BEGIN;
-- Add field lease_execution_revocation_reason to managementagreement
ALTER TABLE "operations_management_agreement" ADD COLUMN "lease_execution_revocation_reason" text DEFAULT '' NOT NULL;
ALTER TABLE "operations_management_agreement" ALTER COLUMN "lease_execution_revocation_reason" DROP DEFAULT;
-- Add field lease_execution_revoked_at to managementagreement
ALTER TABLE "operations_management_agreement" ADD COLUMN "lease_execution_revoked_at" timestamp with time zone NULL;
-- Add field lease_execution_revoked_by to managementagreement
ALTER TABLE "operations_management_agreement" ADD COLUMN "lease_execution_revoked_by_id" uuid NULL CONSTRAINT "operations_managemen_lease_execution_revo_587e8cd8_fk_identity_" REFERENCES "identity_user"("id") DEFERRABLE INITIALLY DEFERRED; SET CONSTRAINTS "operations_managemen_lease_execution_revo_587e8cd8_fk_identity_" IMMEDIATE;
CREATE INDEX "operations_management_agre_lease_execution_revoked_by_587e8cd8" ON "operations_management_agreement" ("lease_execution_revoked_by_id");
COMMIT;
```

---

## 2. Correction 2 — Rentable-Unit Persistence Protection

### 2.1 Enforcement Architecture
To prevent aggregate building leases, subdivided parent property leases, orphan unit leases, or archived property leases from bypassing service-level validation:
1. **Model `.save()` Guard**:
   In `Lease.save()`, when `self._state.adding` is `True`, `validate_rentable_unit(self.property)` is executed before insertion. This protects:
   - Direct `instance.save()`
   - Django ORM `Lease.objects.create(...)`
2. **QuerySet `.bulk_create()` Guard**:
   In `LeaseQuerySet.bulk_create()`, iterates over candidate objects and validates `validate_rentable_unit(obj.property)` prior to executing `super().bulk_create()`.

### 2.2 Remaining Enforcement Boundaries
| Layer | Protected? | Mechanism | Notes |
| :--- | :---: | :--- | :--- |
| **Domain Services** | **YES** | `validate_rentable_unit(prop)` | Standard application entry point for lease creation. |
| **ORM `save()`** | **YES** | `Lease.save(self._state.adding)` | Intercepts single-row creation. |
| **ORM `objects.create()`** | **YES** | Inherits `Lease.save()` | Intercepts standard manager creation. |
| **ORM `bulk_create()`** | **YES** | `LeaseQuerySet.bulk_create()` | Intercepts batch ingestion. |
| **Raw SQL Superuser Bypass** | **NO** | Direct `psql` / raw SQL execution | Standard PostgreSQL behavior; requires superuser access. Database check constraints on cross-table foreign key attributes are avoided due to lack of cross-table subqueries in standard PostgreSQL CHECK constraints. Production mitigation if required: row-level security or custom PostgreSQL triggers. |

---

## 3. Correction 3 — Canonical Contractual Hash Specification

### 3.1 Specification Reconciliation
The specification ambiguity identified in Gate 3 has been formally resolved in:
- `docs/engineering/M7_B_APPROVED_ARCHITECTURE.md` (Section 5.1)
- `docs/engineering/M7_B_IMPLEMENTATION_PLAN.md` (Section 2, `document.py` & `leases.py`)
- `docs/engineering/M7_B_ARCHITECTURE_CHALLENGE.md` (Section 2.4)

### 3.2 Canonical Hash Rules
1. **Agreed Contractual Terms Only**:
   The RFC 8785 canonical JSON payload hashed into `contract_data_hash` covers:
   - Property identity and legal address.
   - Agreed contractual dates: `start_date`, `term_end_date`, `payment_due_day`.
   - Financial obligations: `rent_amount_minor`, `deposit_amount_minor`, `service_charge_minor`, `currency`.
   - Parties roster: Landlords, managing agents, primary tenants, co-tenants, guarantors, occupants.
   - Standard clauses version and custom covenants.
   - Projected rent schedule summary.
2. **Strict Exclusion of Internal Engine Boundaries**:
   `effective_end_date` is strictly an internal temporal database engine boundary (`term_end_date + 1 day` for half-open intervals). It is **strictly excluded** from the signed canonical JSON payload.
3. **Permanent Post-Termination Immutability**:
   When a lease is terminated early, `actual_termination_date` is recorded and `effective_end_date` is truncated in PostgreSQL. The signed `contract_data_hash` and `rendered_document_hash` remain permanently unmodified, preserving cryptographic evidence of the original bilateral contract as executed.

---

## 4. Correction 4 — Early Termination & Temporal Exclusion Integrity

### 4.1 GiST Exclusion Range Semantics
In `apps/tenancy/domain.py`, `OCCUPANCY_RESERVING` statuses were expanded:
```python
OCCUPANCY_RESERVING = frozenset({
    PENDING_SIGNATURE,
    FULLY_EXECUTED,
    ACTIVE,
    UNDER_RENEWAL,
    TERMINATED_NORMAL,
    TERMINATED_EARLY,
})
```

### 4.2 Early Termination Lifecycle & Two-Way Protection
1. **Truncation of Interval**:
   Upon early termination on `actual_termination_date` (e.g. `2026-06-30`):
   - `lease.actual_termination_date = 2026-06-30`
   - `lease.effective_end_date = 2026-07-01` (`actual_termination_date + 1 day`)
   - `lease.status = LeaseStatus.TERMINATED_EARLY`
2. **Historical Collision Block**:
   Because `TERMINATED_EARLY` remains in the GiST exclusion constraint, the historical interval $[2026-01-01, 2026-07-01)$ remains indexed. Any fraudulent or erroneous attempt to insert a backdated lease overlapping with that historical period immediately triggers a PostgreSQL `IntegrityError` (`tenancy_lease_exclude_overlapping_contractual_leases`).
3. **Future Timeline Release**:
   Because `effective_end_date` was truncated to `2026-07-01`, all dates from $[2026-07-01, \infty)$ are immediately released. A new replacement lease starting on `2026-07-01` commits cleanly without exclusion violation.

### 4.3 Migration: `tenancy.0003_expand_temporal_exclusion_to_terminated_leases`
```sql
BEGIN;
-- Remove constraint tenancy_lease_exclude_overlapping_contractual_leases from model lease
ALTER TABLE "tenancy_lease" DROP CONSTRAINT "tenancy_lease_exclude_overlapping_contractual_leases";
-- Create constraint tenancy_lease_exclude_overlapping_contractual_leases on model lease
ALTER TABLE "tenancy_lease" ADD CONSTRAINT "tenancy_lease_exclude_overlapping_contractual_leases"
EXCLUDE USING GIST ("property_id" WITH =, (daterange("start_date", "effective_end_date", '[)')) WITH &&)
WHERE ("status" IN ('active', 'fully_executed', 'pending_signature', 'terminated_early', 'terminated_normal', 'under_renewal'));
COMMIT;
```

---

## 5. Verification & Test Evidence

### 5.1 Remediation Regression Test Suite (`test_m7b_phase2a_remediation.py`)
Execution command: `uv run pytest tests/integration/test_m7b_phase2a_remediation.py`
Result: **14 passed in 7.68s**

| Test Name | Area | Verification Purpose | Result |
| :--- | :--- | :--- | :---: |
| `test_mandated_managing_agent_possesses_lease_execution_authority` | Area 1 | Proposing with `can_execute_leases=True` confers authority upon activation. | **PASS** |
| `test_ordinary_agent_without_mandate_lacks_lease_execution_authority` | Area 1 | Standard agent (`can_execute_leases=False`) fails authority check. | **PASS** |
| `test_unauthorized_mutation_of_can_execute_leases_is_blocked` | Area 1 | Direct instance `.save()` or QuerySet `.update()` raises `ValidationError`. | **PASS** |
| `test_agent_cannot_grant_themselves_authority_unilaterally` | Area 1 | Agent proposing execution mandate requires explicit owner acceptance. | **PASS** |
| `test_owner_explicit_revocation_of_lease_execution_authority` | Area 1 | Revocation sets `revoked_at`, preserves `can_execute_leases=True`, and blocks clearing timestamps. | **PASS** |
| `test_agreement_termination_revokes_lease_execution_authority` | Area 1 | Agreement termination preserves contractual history while revoking effective authority. | **PASS** |
| `test_lease_execution_authority_scoped_strictly_to_agreement_property` | Area 1 | Execution mandate for Property A cannot be exercised on Property B. | **PASS** |
| `test_canonical_rentable_unit_validation_paths` | Area 2 | Domain validation rejects aggregates, subdivided parents, orphans, archived units. | **PASS** |
| `test_lease_orm_creation_enforces_rentable_unit` | Area 2 | Model `.save()`, `objects.create()`, and `bulk_create()` reject unrentable units. | **PASS** |
| `test_executed_lease_immutability_guards` | Area 3 | Executed lease contractual terms cannot be mutated or deleted. | **PASS** |
| `test_lease_party_immutability_and_deletion_guards` | Area 3 | Lease parties cannot be deleted or mutated post-execution. | **PASS** |
| `test_signature_evidence_uniqueness_and_idempotency` | Area 3 | Signature evidence enforces `(lease, party, document_version)` uniqueness. | **PASS** |
| `test_authorized_occupant_cannot_sign_evidence` | Area 3 | Check constraint blocks authorized occupants from signing evidence. | **PASS** |
| `test_early_termination_temporal_exclusion_preservation` | Area 4 | Early terminated lease protects historical range from collisions while releasing future dates. | **PASS** |

### 5.2 Tenancy Model Test Suite (`test_tenancy_models.py`)
Execution command: `uv run pytest tests/integration/test_tenancy_models.py`
Result: **13 passed in 8.95s**
- Same-day turnovers $[T_1, T_2)$ and $[T_2, T_3)$ coexist cleanly.
- Overlapping active/pending reservations trigger PostgreSQL GiST `IntegrityError`.
- Open-ended periodic leases ($[T_1, \infty)$) safely reserve indefinite future timelines.

### 5.3 System Check & Migrations Dry-Run
- `uv run python manage.py check`: **0 issues identified**.
- `uv run python manage.py makemigrations --check --dry-run`: **No changes detected**.

---

## 6. Modified & Created Files Inventory

### Application Source Code
1. `apps/operations/models.py`:
   - Added `lease_execution_revoked_at`, `lease_execution_revoked_by`, `lease_execution_revocation_reason` to `ManagementAgreement`.
   - Added `@builtins.property def has_effective_lease_execution_authority(self) -> bool`.
   - Enforced immutable `can_execute_leases` and irrevocable `lease_execution_revoked_at` in `save()` and QuerySet `update()`.
2. `apps/operations/services/agreements.py`:
   - Updated `revoke_lease_execution_authority()` and `terminate_agreement()` to record audit columns without mutating `can_execute_leases`.
   - Updated `verify_lease_execution_authority()` to evaluate `has_effective_lease_execution_authority`.
3. `apps/operations/serializers.py`:
   - Exposed revocation audit fields and `has_effective_lease_execution_authority`.
4. `apps/operations/admin.py`:
   - Added revocation audit fields to `readonly_fields`.
5. `apps/tenancy/models.py`:
   - Added `validate_rentable_unit` guards to `Lease.save()` and `LeaseQuerySet.bulk_create()`.
6. `apps/tenancy/domain.py`:
   - Updated `LeaseStatus.OCCUPANCY_RESERVING` to include `TERMINATED_NORMAL` and `TERMINATED_EARLY`.

### Migrations
7. `apps/operations/migrations/0003_add_lease_execution_revocation_evidence.py`
8. `apps/tenancy/migrations/0003_expand_temporal_exclusion_to_terminated_leases.py`

### Specifications & Documentation
9. `docs/engineering/M7_B_APPROVED_ARCHITECTURE.md`:
   - Updated Section 4.3 (GiST exclusion for terminated leases and historical protection).
   - Updated Section 5.1 (Canonical RFC 8785 JSON hash definition; strict exclusion of `effective_end_date`).
10. `docs/engineering/M7_B_IMPLEMENTATION_PLAN.md`:
    - Updated Section 2 (`document.py` hash immutability; `leases.py` early termination lifecycle).
11. `docs/engineering/M7_B_ARCHITECTURE_CHALLENGE.md`:
    - Updated Sections 2.3 and 2.4 (Reconciliation of dual hashing and early termination GiST reservation).
12. `docs/engineering/M7_B_FINAL_INTEGRITY_CORRECTIONS_REPORT.md` (This document).

### Test Suites
13. `tests/integration/test_m7b_phase2a_remediation.py`:
    - Updated revocation tests to verify `can_execute_leases=True` preservation and revocation evidence.
    - Added rentable-unit ORM persistence tests (`test_lease_orm_creation_enforces_rentable_unit`).
    - Added early termination GiST exclusion tests (`test_early_termination_temporal_exclusion_preservation`).

---

## 7. Final Certification & Phase 2B Authorization Readiness

### Verification Verdict: **PASS**

All four gate items are resolved:
1. Contractual authority history is permanently preserved on the agreement row while revocation evidence is auditable and effective authority is securely evaluated.
2. Rentable-unit integrity is enforced across all ORM creation paths (`save()`, `objects.create()`, `bulk_create()`).
3. Cryptographic hash specifications cleanly decouple bilateral contractual terms from internal database engine dates.
4. Early and normal lease terminations maintain historical GiST exclusion protection against backdated collisions while instantly releasing future inventory.

**Phase 2B (Tenancy Domain Services, Temporal State Machine & Pre-Reservation Engine) is ready for implementation authorization.**
