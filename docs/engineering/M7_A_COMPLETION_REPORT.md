# GLITTERS CORE — Milestone M7-A Completion Report: Operational Foundations & Management Agreements

**Document Path**: `docs/engineering/M7_A_COMPLETION_REPORT.md`
**Milestone**: Milestone 7-A (Operational Foundations & Management Authority Core)
**Date**: November 12, 2026
**Status**: COMPLETE & VERIFIED
**Author**: Principal Django Backend Engineer & Lead Architect
**Repository**: `glitters-core` ([/home/mr-robot/Projects/glitters-core](file:///home/mr-robot/Projects/glitters-core))
**Companion Specifications**:
- [M7_APPROVED_ARCHITECTURE.md](file:///home/mr-robot/Projects/glitters-core/docs/engineering/M7_APPROVED_ARCHITECTURE.md)
- [M7_IMPLEMENTATION_PLAN.md](file:///home/mr-robot/Projects/glitters-core/docs/engineering/M7_IMPLEMENTATION_PLAN.md)

---

## 1. Executive Summary

Sub-Milestone **M7-A** delivers the operational and database foundations for the GLITTERS Property Management Operating System (PMOS). It introduces:
1. The **PostgreSQL `btree_gist` extension** enabling composite GiST exclusion indexing for future tenancy date-range overlap constraints.
2. The **`apps.operations`** domain module with bilateral `ManagementAgreement` state machines and anti-hijacking authority verification.
3. **Delegated Property Access** provisioning property-scoped operational authority without mutating canonical `Property.management_context` or `managing_organization`.
4. **Unit Physical Readiness** (`UnitPhysicalProfile`) tracking turnover and inspection states independently of contractual lease occupancy.
5. **Append-Only Operational Auditing** (`OperationsEvent`) with deterministic SHA-256 idempotency key deduplication.

All 10 acceptance criteria have been verified against source code and automated tests.

---

## 2. Phase-by-Phase Implementation Deliverables

### 2.1 Phase 0 — Baseline Verification
- Working tree inspected: branch `feature/media-infrastructure`.
- Confirmed unapplied migrations (`marketplace.0003`, `solutions.0001`, `solutions.0002`) remain isolated.
- Verified existing extensions (`plpgsql`, `postgis`, `postgis_topology`, `fuzzystrmatch`, `postgis_tiger_geocoder`).
- Verified `PropertyAccess` provenance, `PropertyParty` claim mechanics, central RBAC matrix, and `property_management_context` check constraints.
- Confirmed zero blockers prior to modification.

### 2.2 Phase 1 — Database Prerequisites
- Created migration `apps.core.migrations.0002_enable_btree_gist.py` using `django.contrib.postgres.operations.BtreeGistExtension()`.
- Successfully applied migration to the PostgreSQL development database; verified `btree_gist` present in `pg_extension`.
- Guaranteed migration consistency and dependency ordering for downstream tenancy models.

### 2.3 Phase 2 — Management Agreements Domain
- Established `apps/operations/domain.py` specifying `AgreementStatus` choices (`DRAFT`, `PENDING_OWNER_ACCEPTANCE`, `PENDING_AGENT_ACCEPTANCE`, `ACCEPTED`, `ACTIVE`, `REJECTED`, `REVOKED`, `TERMINATED`, `EXPIRED`) and formal state transitions.
- Implemented `ManagementAgreement` model in `apps/operations/models.py` with check constraints for non-negative spend limits, basis-point commission range ($0 - 100\%$), date validity, and partial unique constraint enforcing a single active agreement per property.
- Implemented bilateral authority verification services in `apps/operations/services/agreements.py`:
  - `verify_owner_authority`: Strictly enforces active direct `PropertyAccess(capability="manage")` or corporate ownership; rejects unverified `PropertyParty` claims.
  - `verify_agent_authority`: Validates active `OrganizationMembership` holding `operations.manage`.
  - Bilateral handshake: Supports proposals initiated by either owner or PM firm, requiring formal acceptance by the counterparty before activation.
  - Controlled activation: Validates effective date window (`valid_from <= today <= valid_until`).
  - Formal cancellation: Supports `reject` (counterparty), `revoke` (proposer), and `terminate` (either party when active).

### 2.4 Phase 3 — Delegated Property Access
- Implemented `DelegatedPropertyAccess` in `apps/operations/models.py` linking `ManagementAgreement` to `PropertyAccess`.
- On agreement activation: Atomically provisions inherited `PropertyAccess(capability="manage", origin="inherited")` for the assigned managing agent.
- **Preservation of M4 Immutability**: Personal properties maintain `management_context="personal"` and `managing_organization=None`. Operational authority is delegated purely through the explicit access grant.
- On agreement termination: Atomically revokes `DelegatedPropertyAccess` and cascades revocation to the underlying `PropertyAccess` grant.

### 2.5 Phase 4 — Unit Operational Profiles
- Implemented `UnitPhysicalProfile` in `apps/operations/models.py` tracking physical asset readiness:
  - States: `READY_INSPECTED`, `TURNOVER_PREPARATION`, `REPAIR_HOLD`, `STRUCTURAL_OFFLINE`, `OWNER_RESERVED`.
  - Timestamping: Setting `READY_INSPECTED` atomically populates `ready_at = timezone.now()`.
  - **Zero Lease Occupancy Columns**: Removed denormalized `is_occupied`, `current_lease_id`, and `occupancy_status`. Contractual occupancy belongs strictly to `apps.tenancy`.
- Enforced state transition restrictions via `physical_state_transition_allowed`.
- Implemented `OperationsEvent` inheriting `AppendOnlyQuerySet` which raises `ValueError` on any `.update()` or `.delete()` invocation.

### 2.6 Phase 5 — HTTP REST API
Integrated endpoints under `/api/v1/operations/`:
- `GET, POST /api/v1/operations/agreements/`: List and propose management agreements.
- `GET /api/v1/operations/agreements/<id>/`: Detail view with organization/owner isolation.
- `POST /api/v1/operations/agreements/<id>/accept/`: Counterparty acceptance.
- `POST /api/v1/operations/agreements/<id>/reject/`: Counterparty rejection with optional reason.
- `POST /api/v1/operations/agreements/<id>/activate/`: Controlled activation and delegation provisioning.
- `POST /api/v1/operations/agreements/<id>/revoke/`: Proposer proposal withdrawal.
- `POST /api/v1/operations/agreements/<id>/terminate/`: Agreement termination and access revocation.
- `GET /api/v1/operations/portfolio/`: Managed portfolio view aggregating managed properties with physical readiness state.
- `GET, PATCH /api/v1/operations/units/<property_id>/physical-profile/`: Physical readiness inspection and turnover state management.

---

## 3. Database Migrations Applied

| App | Migration | Operations | Dependencies |
| :--- | :--- | :--- | :--- |
| `core` | `0002_enable_btree_gist` | `BtreeGistExtension()` | `('core', '0001_enable_postgis')` |
| `operations` | `0001_initial` | `CreateModel(ManagementAgreement)`<br>`CreateModel(DelegatedPropertyAccess)`<br>`CreateModel(OperationsEvent)`<br>`CreateModel(UnitPhysicalProfile)`<br>Constraints & Indexes | `('core', '0002_enable_btree_gist')`<br>`('organizations', '0001_initial')`<br>`('properties', '0002_access_provenance')` |

Verification: `python manage.py makemigrations --check --dry-run` reports **"No changes detected"**.

---

## 4. Test Verification & Coverage Analysis

### 4.1 Test Run Summary

```
============================= test session starts ==============================
platform linux -- Python 3.14.4, pytest-9.2.0, pluggy-1.7.0
django: version: 5.2.19, settings: config.settings.development
rootdir: /home/mr-robot/Projects/glitters-core
collected 302 items

302 passed in 74.58s (0:01:14)
======================== 302 passed in 74.58s ========================
```

- **Discovered Tests**: 302
- **Passed**: 302 (100%)
- **Failed**: 0
- **Skipped**: 0
- **Baseline Preserved**: All 276 baseline tests passed without regression.
- **New M7-A Tests Added**: 26 passed.

### 4.2 Breakdown of New M7-A Tests (26 Tests)

| Test File | Test Count | Scope |
| :--- | :---: | :--- |
| `tests/unit/test_operations_domain.py` | 6 | Agreement reference formatting, agreement status transitions, physical state transitions, idempotency key determinism, metadata sanitization, `OperationsEvent` append-only immutability. |
| `tests/unit/test_operations_services.py` | 10 | Anti-hijacking ownership check, owner initiation & agent acceptance, agent initiation & owner acceptance, future start date activation block, termination access revocation, rejection/revocation lifecycles, physical profile transitions, past date expiration block, one-active-agreement constraint, managing agent physical profile update. |
| `tests/integration/test_operations_api.py` | 4 | API proposal & acceptance handshake, API rejection, API termination, API unit physical profile read & patch. |
| `tests/security/test_operations_security.py` | 6 | PostgreSQL `btree_gist` extension verification, cross-organization agreement isolation, anti-hijacking imposter claim block, property management context immutability, migration consistency check, tenant portfolio isolation. |

### 4.3 Code Coverage Impact

| Module | Statements | Missed | Coverage |
| :--- | :---: | :---: | :---: |
| `apps/operations/admin.py` | 30 | 0 | 100% |
| `apps/operations/apps.py` | 6 | 0 | 100% |
| `apps/operations/domain.py` | 88 | 5 | 94% |
| `apps/operations/errors.py` | 14 | 3 | 79% |
| `apps/operations/models.py` | 94 | 3 | 97% |
| `apps/operations/serializers.py` | 55 | 0 | 100% |
| `apps/operations/services/agreements.py` | 291 | 85 | 71% |
| `apps/operations/services/audit.py` | 27 | 5 | 81% |
| `apps/operations/services/physical_profiles.py` | 83 | 16 | 81% |
| `apps/operations/urls.py` | 4 | 0 | 100% |
| `apps/operations/views.py` | 130 | 15 | 88% |
| `apps/core/migrations/0002_enable_btree_gist.py` | 5 | 0 | 100% |
| `apps/operations/migrations/0001_initial.py` | 9 | 0 | 100% |
| **Entire Codebase Total** | **10,416** | **1,276** | **88%** |

---

## 5. Verification of Acceptance Criteria

| # | Acceptance Criterion | Verification Evidence | Status |
| :--- | :--- | :--- | :---: |
| 1 | Management authority cannot be hijacked through unverified ownership claims | `verify_owner_authority` requires direct active `PropertyAccess(capability="manage")`. `PropertyParty.status="claimed"` is explicitly rejected in `test_anti_hijacking_unverified_claimant_cannot_propose` and `test_anti_hijacking_unverified_property_party_claimant_blocked`. | **VERIFIED** |
| 2 | Management agreements follow the approved lifecycle | Tested transitions across `DRAFT`, `PENDING_*`, `ACCEPTED`, `ACTIVE`, `REJECTED`, `REVOKED`, `TERMINATED`, `EXPIRED` in unit and API integration tests. | **VERIFIED** |
| 3 | Delegated access is property-scoped and revocable | `DelegatedPropertyAccess` and inherited `PropertyAccess` created upon activation and revoked atomically upon termination. | **VERIFIED** |
| 4 | Property management context remains immutable | `Property.management_context="personal"` and `Property.managing_organization=None` verified unchanged after agreement creation, activation, and termination in `test_property_management_context_and_org_remain_immutable`. | **VERIFIED** |
| 5 | Operational readiness is independent of lease occupancy | `UnitPhysicalProfile` models only `physical_state`, `ready_at`, `target_rent_minor`, and `target_currency` with zero lease or tenant columns. | **VERIFIED** |
| 6 | Audit records are immutable | `OperationsEvent` inherits `AppendOnlyQuerySet`. Tested `.update()`, `.delete()`, and `.save()` raises `ValueError`. | **VERIFIED** |
| 7 | Existing authorization tests remain passing | All 276 preexisting unit, integration, and security tests continue to pass without regression. | **VERIFIED** |
| 8 | New M7-A tests pass | All 26 new M7-A tests pass (100% pass rate). | **VERIFIED** |
| 9 | Django system checks pass | `python manage.py check` reports 0 issues. | **VERIFIED** |
| 10 | Migration consistency checks pass | `python manage.py makemigrations --check --dry-run` reports "No changes detected". | **VERIFIED** |

---

## 6. Outstanding Architectural Risks & Readiness for M7-B

1. **Agreement Expiration Cron:** In Sub-Milestone M7-E, a recurring Celery Beat task must be scheduled to identify agreements where `valid_until < timezone.now().date()` and transition them from `ACTIVE` to `EXPIRED` while revoking delegated access grants.
2. **Readiness for M7-B (Tenancy & Lease Engine):** With the `btree_gist` extension verified in PostgreSQL and operational management authority established, the platform is fully prepared to implement `apps.tenancy` models (`RentalApplication`, `Lease`, `RentScheduleEntry`) with PostgreSQL `daterange` GiST exclusion constraints.
