# GLITTERS CORE — MILESTONE M3.2.9.1
## Tenancy Domain Regression Investigation and Remediation Audit Report

**Document:** `M3_2_9_1_TENANCY_REGRESSION_REMEDIATION_REPORT.md`
**Classification:** Internal Technical Architecture & Engineering Quality Gate
**Repository:** `~/Projects/glitters-core`
**Date:** 2026-11-14
**Author:** Principal Geospatial Architect & Core Engineering Review Board
**Status:** Remediated & Certified — 100% Pass Rate (361/361 Tests)

---

## 1. Original Failure Investigation

### 1.1 Complete Failure Traceback
During the initial M3.2.9 regression audit, a single test failure occurred in the uncommitted M7 tenancy suite:

```text
==================================== FAILURES ====================================
______________________ test_domain_constants_and_subsets _______________________

    def test_domain_constants_and_subsets():
        assert LeaseStatus.DRAFT in LeaseStatus.MUTABLE_STATUSES
        assert LeaseStatus.PENDING_SIGNATURE in LeaseStatus.MUTABLE_STATUSES
        assert LeaseStatus.ACTIVE not in LeaseStatus.MUTABLE_STATUSES
        assert LeaseStatus.FULLY_EXECUTED not in LeaseStatus.MUTABLE_STATUSES
        assert LeaseStatus.TERMINATED_NORMAL in LeaseStatus.TERMINAL_STATUSES
        assert LeaseStatus.TERMINATED_EARLY in LeaseStatus.TERMINAL_STATUSES
        assert LeaseStatus.CANCELLED in LeaseStatus.TERMINAL_STATUSES
        assert LeaseStatus.EXPIRED_UNSIGNED in LeaseStatus.TERMINAL_STATUSES
        assert LeaseStatus.ACTIVE not in LeaseStatus.TERMINAL_STATUSES

        assert LeaseStatus.PENDING_SIGNATURE in LeaseStatus.OCCUPANCY_RESERVING
        assert LeaseStatus.FULLY_EXECUTED in LeaseStatus.OCCUPANCY_RESERVING
        assert LeaseStatus.ACTIVE in LeaseStatus.OCCUPANCY_RESERVING
        assert LeaseStatus.UNDER_RENEWAL in LeaseStatus.OCCUPANCY_RESERVING
        assert LeaseStatus.DRAFT not in LeaseStatus.OCCUPANCY_RESERVING
        assert LeaseStatus.CANCELLED not in LeaseStatus.OCCUPANCY_RESERVING
        assert LeaseStatus.EXPIRED_UNSIGNED not in LeaseStatus.OCCUPANCY_RESERVING
>       assert LeaseStatus.TERMINATED_NORMAL not in LeaseStatus.OCCUPANCY_RESERVING
E       AssertionError: assert 'terminated_normal' not in frozenset({'terminated_normal', 'active', 'fully_executed', 'pending_signature', 'terminated_early', 'under_renewal'})
E        +  where 'terminated_normal' = <LeaseStatus.TERMINATED_NORMAL: 'terminated_normal'>
E        +  and   frozenset({'terminated_normal', 'active', 'fully_executed', 'pending_signature', 'terminated_early', 'under_renewal'}) = LeaseStatus.OCCUPANCY_RESERVING

tests/unit/test_tenancy_domain.py:77: AssertionError
=========================== short test summary info ============================
FAILED tests/unit/test_tenancy_domain.py::test_domain_constants_and_subsets - AssertionError: assert 'terminated_normal' not in frozenset({'terminated_normal', 'active', 'fully_executed', 'pending_signature', 'terminated_early', 'under_renewal'})
```

### 1.2 Exact Expected vs. Actual Values
- **Target Expression:** `assert LeaseStatus.TERMINATED_NORMAL not in LeaseStatus.OCCUPANCY_RESERVING`
- **Expected by Test Assertion:** `LeaseStatus.TERMINATED_NORMAL` must NOT be in `LeaseStatus.OCCUPANCY_RESERVING`.
- **Actual Runtime Value:** `LeaseStatus.OCCUPANCY_RESERVING` evaluates to:
  `frozenset({'active', 'fully_executed', 'pending_signature', 'terminated_early', 'terminated_normal', 'under_renewal'})`.
- **Result:** `AssertionError` at line 77.

### 1.3 Root Cause & Origin Lineage
The failure did **not** originate from any Milestone M3.2 modification, nor did it represent broken runtime behavior. It was caused by an **outdated test assumption** that predated Milestone M3.2.

1. **Pre-M3.2 Genesis:** During Milestone M7.B (Phase 2A, authored on 2026-11-12), the tenancy domain model was enhanced with migration `apps/tenancy/migrations/0003_expand_temporal_exclusion_to_terminated_leases.py`. This migration added a PostgreSQL GiST exclusion constraint (`exclude_overlapping_lease_periods_per_property`) to ensure that historical periods occupied by terminated leases (`TERMINATED_NORMAL` and `TERMINATED_EARLY`) cannot be overlapped by new or conflicting leases.
2. **Domain Constant Expansion:** To align with this database-level GiST exclusion constraint, `LeaseStatus.OCCUPANCY_RESERVING` in `apps/tenancy/domain.py` was updated on 2026-11-12 to include both `TERMINATED_NORMAL` and `TERMINATED_EARLY`.
3. **Recorded Known Gap (ACT-01):** In `docs/engineering/M7_B_RELEASE_VERIFICATION_REPORT.md` (dated 2026-11-12), under Section 8 ("Remediation Action Items"), item `ACT-01` explicitly stated:
   > *"Update `tests/unit/test_tenancy_domain.py:77-78` to reflect `LeaseStatus.TERMINATED_NORMAL` and `TERMINATED_EARLY` membership in `LeaseStatus.OCCUPANCY_RESERVING` as required by Migration 0003."*
4. **Conclusion:** The test in `tests/unit/test_tenancy_domain.py` had remained in its pre-migration state, asserting obsolete negative membership against an updated domain model.

---

## 2. Test Modification Audit

### 2.1 Exact Diff
The file `tests/unit/test_tenancy_domain.py` is part of the untracked M7 worktree branch `feature/m7-tenancy-operations-preservation`. The contextual difference applied is:

```diff
--- a/tests/unit/test_tenancy_domain.py
+++ b/tests/unit/test_tenancy_domain.py
@@ -74,8 +74,8 @@ def test_domain_constants_and_subsets():
     assert LeaseStatus.DRAFT not in LeaseStatus.OCCUPANCY_RESERVING
     assert LeaseStatus.CANCELLED not in LeaseStatus.OCCUPANCY_RESERVING
     assert LeaseStatus.EXPIRED_UNSIGNED not in LeaseStatus.OCCUPANCY_RESERVING
-    assert LeaseStatus.TERMINATED_NORMAL not in LeaseStatus.OCCUPANCY_RESERVING
-    assert LeaseStatus.TERMINATED_EARLY not in LeaseStatus.OCCUPANCY_RESERVING
+    assert LeaseStatus.TERMINATED_NORMAL in LeaseStatus.OCCUPANCY_RESERVING
+    assert LeaseStatus.TERMINATED_EARLY in LeaseStatus.OCCUPANCY_RESERVING

     assert LeasePartyRole.PRIMARY_TENANT in LeasePartyRole.SIGNATORY_ROLES
     assert LeasePartyRole.CO_TENANT in LeasePartyRole.SIGNATORY_ROLES
```

### 2.2 Line-by-Line Explanation of Changes
- **Line 77:** Changed `assert LeaseStatus.TERMINATED_NORMAL not in LeaseStatus.OCCUPANCY_RESERVING` to `assert LeaseStatus.TERMINATED_NORMAL in LeaseStatus.OCCUPANCY_RESERVING`.
  - *Reasoning:* A lease terminated under normal expiry holds physical and temporal occupancy rights for the executed date range of its tenancy. Including it in `OCCUPANCY_RESERVING` prevents retroactive or concurrent double-booking of physical property units.
- **Line 78:** Changed `assert LeaseStatus.TERMINATED_EARLY not in LeaseStatus.OCCUPANCY_RESERVING` to `assert LeaseStatus.TERMINATED_EARLY in LeaseStatus.OCCUPANCY_RESERVING`.
  - *Reasoning:* An early-terminated lease still legally reserves and accounts for occupancy during its effective operational period prior to early termination.

### 2.3 Assertion Integrity Audit
- **Assertions Removed:** 0.
- **Assertions Weakened:** 0.
- **Assertions Skipped / Bypassed:** 0.
- **Contract Strictness:** The assertions were converted from outdated negative assertions into positive invariant checks that enforce adherence to the PostgreSQL GiST exclusion constraint condition.

### 2.4 Domain Meaning & Established Contract of `OCCUPANCY_RESERVING`
In GLITTERS tenancy architecture:
1. `DRAFT`, `CANCELLED`, and `EXPIRED_UNSIGNED` leases **never** reserve occupancy because they were never executed.
2. `PENDING_SIGNATURE`, `FULLY_EXECUTED`, `ACTIVE`, `UNDER_RENEWAL`, `TERMINATED_NORMAL`, and `TERMINATED_EARLY` **do** reserve occupancy across their respective effective date ranges:
   - Active/Pending leases block future/current double-booking.
   - Terminated leases block retroactive tampering or overlapping double-booking over historical periods.
3. This contract is mirrored across:
   - `apps/tenancy/domain.py` (`LeaseStatus.OCCUPANCY_RESERVING`)
   - `apps/tenancy/models.py` (`exclude_overlapping_lease_periods_per_property` constraint)
   - `apps/tenancy/migrations/0003_expand_temporal_exclusion_to_terminated_leases.py`

---

## 3. Isolated Verification

Execution command:
```bash
uv run pytest tests/unit/test_tenancy_domain.py -vv
```

### Complete Output:
```text
============================= test session starts ==============================
platform linux -- Python 3.14.4, pytest-9.2.0, pluggy-1.7.0 -- /home/mr-robot/Projects/glitters-core/.venv/bin/python
cachedir: .pytest_cache
django: version: 5.2.19, settings: config.settings.development (from ini)
rootdir: /home/mr-robot/Projects/glitters-core
configfile: pyproject.toml
plugins: cov-7.1.0, django-4.14.0, platformdirs-4.14.5
collecting ... collected 6 items

tests/unit/test_tenancy_domain.py::test_reference_generators PASSED      [ 16%]
tests/unit/test_tenancy_domain.py::test_domain_constants_and_subsets PASSED [ 33%]
tests/unit/test_tenancy_domain.py::test_physical_occupancy_state_transitions PASSED [ 50%]
tests/unit/test_tenancy_domain.py::test_management_agreement_can_execute_leases_field PASSED [ 66%]
tests/unit/test_tenancy_domain.py::test_lease_property_unit_validation PASSED [ 83%]
tests/unit/test_tenancy_domain.py::test_tenancy_event_append_only_immutability PASSED [100%]

============================== 6 passed in 22.56s ==============================
```
- **Exit Code:** `0`
- **Result:** 6 passed, 0 failed.

---

## 4. Full Application Regression Audit

Execution command:
```bash
uv run pytest -ra
```

### Summary of Full Test Suite Results:
- **Total Collected:** 361
- **Passed:** 361
- **Failed:** 0
- **Skipped:** 0
- **Errors:** 0
- **Failure Tracebacks:** None
- **Execution Time:** 160.74s (02:40)
- **Exit Code:** `0`

### Per-Package Breakdown:
- `tests/integration/test_enquiry_inbox.py`: 12 passed
- `tests/integration/test_enquiry_lifecycle.py`: 11 passed
- `tests/integration/test_grant_provenance.py`: 2 passed
- `tests/integration/test_health.py`: 8 passed
- `tests/integration/test_identity_api.py`: 23 passed
- `tests/integration/test_locations_api.py`: 4 passed
- `tests/integration/test_m7b_phase2a_remediation.py`: 14 passed
- `tests/integration/test_marketplace_api.py`: 8 passed
- `tests/integration/test_marketplace_authorization.py`: 7 passed
- `tests/integration/test_media_uploads.py`: 10 passed
- `tests/integration/test_operations_api.py`: 4 passed
- `tests/integration/test_organizations_api.py`: 16 passed
- `tests/integration/test_properties_api.py`: 5 passed
- `tests/integration/test_public_enquiries.py`: 25 passed
- `tests/integration/test_solutions_workflows.py`: 7 passed
- `tests/integration/test_spatial_api.py`: 3 passed
- `tests/integration/test_spatial_models.py`: 7 passed
- `tests/integration/test_tenancy_models.py`: 13 passed
- `tests/security/test_baseline.py`: 6 passed
- `tests/security/test_identity_security.py`: 6 passed
- `tests/security/test_invitation_reactivation.py`: 4 passed
- `tests/security/test_marketplace_privacy.py`: 4 passed
- `tests/security/test_operations_security.py`: 10 passed
- `tests/security/test_organizations_security.py`: 8 passed
- `tests/security/test_properties_security.py`: 14 passed
- `tests/security/test_spatial_tenancy.py`: 3 passed
- `tests/unit/test_api_errors.py`: 1 passed
- `tests/unit/test_enquiry_access.py`: 4 passed
- `tests/unit/test_enquiry_input.py`: 5 passed
- `tests/unit/test_enquiry_persistence.py`: 37 passed
- `tests/unit/test_identity.py`: 9 passed
- `tests/unit/test_identity_domain.py`: 13 passed
- `tests/unit/test_media_storage.py`: 6 passed
- `tests/unit/test_operations_domain.py`: 6 passed
- `tests/unit/test_operations_services.py`: 10 passed
- `tests/unit/test_organizations_domain.py`: 4 passed
- `tests/unit/test_property_rules.py`: 8 passed
- `tests/unit/test_request_id.py`: 2 passed
- `tests/unit/test_solutions_domain.py`: 7 passed
- `tests/unit/test_spatial_crs.py`: 3 passed
- `tests/unit/test_spatial_domain.py`: 6 passed
- `tests/unit/test_tenancy_domain.py`: 6 passed

---

## 5. Independent Spatial Test Suite Verification

Execution command:
```bash
uv run pytest tests/unit/test_spatial_crs.py tests/unit/test_spatial_domain.py tests/integration/test_spatial_api.py tests/integration/test_spatial_models.py tests/security/test_spatial_tenancy.py -ra
```

### Output:
```text
============================= test session starts ==============================
platform linux -- Python 3.14.4, pytest-9.2.0, pluggy-1.7.0
django: version: 5.2.19, settings: config.settings.development (from ini)
rootdir: /home/mr-robot/Projects/glitters-core
configfile: pyproject.toml
plugins: cov-7.1.0, django-4.14.0, platformdirs-4.14.5
collecting ... collected 22 items

tests/integration/test_spatial_api.py ...                                [ 13%]
tests/integration/test_spatial_models.py .......                         [ 45%]
tests/security/test_spatial_tenancy.py ...                               [ 59%]
tests/unit/test_spatial_crs.py ...                                       [ 72%]
tests/unit/test_spatial_domain.py ......                                 [100%]

============================= 22 passed in 19.83s ==============================
```
- **Exit Code:** `0`
- **Spatial Tests:** 22/22 passed (100%).

---

## 6. Migration & Static System Checks

### 6.1 Django System Check
```bash
uv run python manage.py check
```
**Output:**
```text
System check identified no issues (0 silenced).
```
**Exit Code:** `0`

### 6.2 Migration Integrity & Drift Check
```bash
uv run python manage.py makemigrations --check --dry-run
```
**Output:**
```text
No changes detected
```
**Exit Code:** `0`

---

## 7. Worktree Preservation & Diff Audit

### 7.1 `git status --short`
```text
 M apps/marketplace/domain.py
 M apps/marketplace/filters.py
 M apps/marketplace/models.py
 M apps/marketplace/serializers.py
 M apps/marketplace/services/access.py
 M apps/marketplace/services/representation.py
 M apps/marketplace/urls.py
 M apps/marketplace/views.py
 M apps/organizations/domain.py
 M apps/properties/services/authorization.py
 M config/settings/base.py
 M config/urls.py
 docs/api/V1.md
 docs/architecture/RBAC_ARCHITECTURE.md
 tests/conftest.py
 tests/integration/test_organizations_api.py
 tests/unit/test_organizations_domain.py
?? apps/core/migrations/0002_enable_btree_gist.py
?? apps/marketplace/migrations/0003_enquiry_enquiryevent_and_more.py
?? apps/marketplace/services/enquiries.py
?? apps/marketplace/throttles.py
?? apps/operations/
?? apps/solutions/
?? apps/spatial/
?? apps/tenancy/
?? docs/engineering/ECOSYSTEM_TRACEABILITY.md
?? docs/engineering/M3_2_9_DATABASE_INTEGRITY_REPORT.md
?? docs/engineering/M3_2_9_MIGRATION_VERIFICATION_REPORT.md
?? docs/engineering/M3_2_9_REGRESSION_REPORT.md
?? docs/engineering/M3_2_9_SECURITY_VERIFICATION_REPORT.md
?? docs/engineering/M3_2_IMPLEMENTATION_PLAN.md
?? docs/engineering/M3_2_MIGRATION_REPORT.md
?? docs/engineering/M3_2_MIGRATION_STRATEGY.md
?? docs/engineering/M3_2_SCHEMA_DESIGN.md
?? docs/engineering/M3_2_SCHEMA_REPORT.md
?? docs/engineering/M3_2_SECURITY_DESIGN.md
?? docs/engineering/M3_2_SECURITY_REPORT.md
?? docs/engineering/M3_2_TEST_REPORT.md
?? docs/engineering/M6_BASELINE_RECONCILIATION.md
?? docs/engineering/M7_APPROVED_ARCHITECTURE.md
?? docs/engineering/M7_ARCHITECTURE_CHALLENGE.md
?? docs/engineering/M7_A_COMPLETION_REPORT.md
?? docs/engineering/M7_A_SECURITY_REMEDIATION_REPORT.md
?? docs/engineering/M7_B_APPROVED_ARCHITECTURE.md
?? docs/engineering/M7_B_ARCHITECTURE_CHALLENGE.md
?? docs/engineering/M7_B_FINAL_INTEGRITY_CORRECTIONS_REPORT.md
?? docs/engineering/M7_B_IMPLEMENTATION_PLAN.md
?? docs/engineering/M7_B_PHASE1_COMPLETION_REPORT.md
?? docs/engineering/M7_B_PHASE1_FINAL_VERIFICATION.md
?? docs/engineering/M7_B_PHASE2A_FINAL_GATE_REPORT.md
?? docs/engineering/M7_B_PHASE2A_REMEDIATION_REPORT.md
?? docs/engineering/M7_B_RELEASE_VERIFICATION_REPORT.md
?? docs/engineering/M7_IMPLEMENTATION_PLAN.md
?? docs/engineering/M7_PROPERTY_MANAGEMENT_ARCHITECTURE.md
?? docs/engineering/SOLUTIONS_READINESS.md
?? tests/integration/test_enquiry_inbox.py
?? tests/integration/test_enquiry_lifecycle.py
?? tests/integration/test_m7b_phase2a_remediation.py
?? tests/integration/test_operations_api.py
?? tests/integration/test_public_enquiries.py
?? tests/integration/test_solutions_workflows.py
?? tests/integration/test_spatial_api.py
?? tests/integration/test_spatial_models.py
?? tests/integration/test_tenancy_models.py
?? tests/security/test_operations_security.py
?? tests/security/test_spatial_tenancy.py
?? tests/unit/test_enquiry_access.py
?? tests/unit/test_enquiry_input.py
?? tests/unit/test_enquiry_persistence.py
?? tests/unit/test_operations_domain.py
?? tests/unit/test_operations_services.py
?? tests/unit/test_solutions_domain.py
?? tests/unit/test_spatial_crs.py
?? tests/unit/test_spatial_domain.py
?? tests/unit/test_tenancy_domain.py
```

### 7.2 `git diff -- tests/unit/test_tenancy_domain.py`
```bash
git diff -- tests/unit/test_tenancy_domain.py
```
*(Empty output: the file `tests/unit/test_tenancy_domain.py` is an uncommitted/untracked file in the `feature/m7-tenancy-operations-preservation` working tree).*

### 7.3 `git diff --stat`
```text
 apps/marketplace/domain.py                  | 163 +++++++++++++++++++++
 apps/marketplace/filters.py                 |  50 ++++++-
 apps/marketplace/models.py                  | 211 ++++++++++++++++++++++++++++
 apps/marketplace/serializers.py             |  74 +++++++++-
 apps/marketplace/services/access.py         |  63 +++++++++
 apps/marketplace/services/representation.py |  26 ++++
 apps/marketplace/urls.py                    |  30 ++++
 apps/marketplace/views.py                   | 117 ++++++++++++++-
 apps/organizations/domain.py                |  34 ++++-
 apps/properties/services/authorization.py   |  16 +++
 config/settings/base.py                     |   9 ++
 config/urls.py                              |   3 +
 docs/api/V1.md                              |   7 +
 docs/architecture/RBAC_ARCHITECTURE.md      |   8 +-
 tests/conftest.py                           |   1 +
 tests/integration/test_organizations_api.py |   6 +
 tests/unit/test_organizations_domain.py     |   8 ++
 17 files changed, 817 insertions(+), 9 deletions(-)
```

---

## 8. Certification & Acceptance Gate

- [x] Root cause traced and proven to be an obsolete unit test assumption predating M3.2 (documented in `M7_B_RELEASE_VERIFICATION_REPORT.md` as item `ACT-01`).
- [x] Zero assertions removed, weakened, or bypassed.
- [x] Invariant domain contract verified against PostgreSQL GiST exclusion constraints.
- [x] Isolated test suite passed with exit code 0 (`6/6 passed`).
- [x] Full application suite passed with exit code 0 (`361/361 passed`).
- [x] Spatial test suite passed with exit code 0 (`22/22 passed`).
- [x] Django system check identified no issues (`0 silenced`).
- [x] Django migration check identified no unmigrated model changes (`--check --dry-run` passed).
- [x] Repository worktree and branch integrity preserved with zero commits, pushes, stashes, or resets.
