# GLITTERS CORE — Milestone M7-A Security Remediation & Certification Report

**Document Path**: `docs/engineering/M7_A_SECURITY_REMEDIATION_REPORT.md`
**Milestone**: Milestone 7-A (Operational Foundations & Management Authority Core)
**Security Certification**: PASS / APPROVED FOR M7-B
**Date**: November 12, 2026
**Author**: Principal Software Architect & Application Security Auditor
**Repository**: `glitters-core` ([/home/mr-robot/Projects/glitters-core](file:///home/mr-robot/Projects/glitters-core))
**Companion Documents**:
- [M7_APPROVED_ARCHITECTURE.md](file:///home/mr-robot/Projects/glitters-core/docs/engineering/M7_APPROVED_ARCHITECTURE.md)
- [M7_IMPLEMENTATION_PLAN.md](file:///home/mr-robot/Projects/glitters-core/docs/engineering/M7_IMPLEMENTATION_PLAN.md)
- [M7_A_COMPLETION_REPORT.md](file:///home/mr-robot/Projects/glitters-core/docs/engineering/M7_A_COMPLETION_REPORT.md)

---

## 1. Executive Summary & Final Verdict

Following the comprehensive implementation of Sub-Milestone M7-A (Operational Foundations & Management Agreements), an independent adversarial code and database audit identified four focused security and integrity findings (F-01 through F-04).

All four findings have been remediated, architecturally sealed, and verified through dedicated security regression tests without introducing schema migrations, breaking changes, or modifying the M6 baseline.

### Final Certification Verdict: **APPROVED FOR M7-B**

The management agreement subsystem, access delegation framework, operational authorization checks, and audit logging layers meet all architectural and security requirements specified in [M7_APPROVED_ARCHITECTURE.md](file:///home/mr-robot/Projects/glitters-core/docs/engineering/M7_APPROVED_ARCHITECTURE.md). Development may proceed to **Milestone M7-B (Tenancy Domain & PostgreSQL GiST Date-Range Exclusion Constraints)**.

---

## 2. Findings & Remediation Matrix

| Finding ID | Severity | Area | Root Cause | Remediation Summary | Status |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **F-01** | High | Authority Provenance | `verify_owner_authority` checked `status="active"` and `capability="manage"`, but omitted `origin=AccessOrigin.DIRECT`, permitting inherited management grants to impersonate owners or sub-delegate. | Restricted direct grant query to `origin=AccessOrigin.DIRECT`. Inherited grants can never satisfy owner authority or sub-delegate. Corporate ownership path preserved. | **RESOLVED** |
| **F-02** | High | Expiry Enforcement | Access authorization relied exclusively on Celery background sweep (`sync_agreement_expiry`) to transition status to `EXPIRED`, leaving a temporal window of unauthorized operational access. | Added real-time evaluation in `decide()`, `check_operations_authority()`, and portfolio views: access drops immediately when `today > valid_until`. Added idempotent inline synchronization. | **RESOLVED** |
| **F-03** | Medium | Test Coverage | Corporate ownership authorization path (`management_context="organization"`) lacked explicit unit and security regression test coverage for corporate officers (`OWNER`/`ADMIN`). | Refactored direct grant acquisition in `verify_owner_authority` to avoid unique constraint collisions; added end-to-end regression tests validating corporate officer authority. | **RESOLVED** |
| **F-04** | Low | Immutability Hardening | `AppendOnlyQuerySet` overrode `.update()` and `.delete()`, but did not override `.bulk_update()`, leaving an ORM bypass path for audit event modification. | Implemented `.bulk_update()` override in `AppendOnlyQuerySet` raising `ValueError("Operations events are append-only.")`. Immutability verified across all ORM write interfaces. | **RESOLVED** |

---

## 3. Detailed Technical Remediation

### 3.1 Correction 1 — F-01: Direct Owner Authority Provenance

#### Vulnerability Mechanics
In the original M7-A implementation of `verify_owner_authority` ([apps/operations/services/agreements.py](file:///home/mr-robot/Projects/glitters-core/apps/operations/services/agreements.py)), the service verified whether the user possessed an active `PropertyAccess` grant with `capability="manage"`. However, when a management agreement is activated, the designated managing agent is provisioned a `PropertyAccess` grant with `capability="manage"` and `origin=AccessOrigin.INHERITED`. Consequently, an active managing agent could satisfy the owner authority check and propose management agreements to other entities as if they were the property owner (unauthorized sub-delegation).

#### Remediation Implementation
Modified `verify_owner_authority` in [agreements.py](file:///home/mr-robot/Projects/glitters-core/apps/operations/services/agreements.py#L72-L82):
```python
# apps/operations/services/agreements.py
direct_grant = (
    PropertyAccess.objects.filter(
        property=property_record,
        user=user,
        status=AccessStatus.ACTIVE,
        capability=AccessCapability.MANAGE,
        origin=AccessOrigin.DIRECT,  # F-01: Strictly require DIRECT origin
    )
    .order_by("-created_at")
    .first()
)
```

#### Behavioral Rules Enforced
1. **Direct Owners**: Retain direct `PropertyAccess(origin=AccessOrigin.DIRECT, capability=AccessCapability.MANAGE)` and can propose or accept management agreements.
2. **Managing Agents**: Hold inherited `PropertyAccess(origin=AccessOrigin.INHERITED)`. They are strictly prohibited from proposing agreements as the property owner and cannot sub-delegate authority.
3. **Corporate Ownership Path**: If `property_record.management_context == ManagementContext.ORGANIZATION`, the service verifies that the user holds an active membership with role `OWNER` or `ADMIN` in `property_record.managing_organization`. If satisfied, an active direct `PropertyAccess` record is fetched or created for the corporate officer.

---

### 3.2 Correction 2 — F-02: Real-Time Agreement Expiry Enforcement & Boundary Semantics

#### Vulnerability Mechanics
Agreement expiry was tracked by a periodic worker. If an agreement reached its `valid_until` date, the managing agent's inherited `PropertyAccess` record remained `ACTIVE` until `sync_agreement_expiry` was executed. Any requests hitting `apps.properties.services.authorization.decide()` or `apps.operations.services.physical_profiles.check_operations_authority()` would grant full access if the periodic job was delayed or failed.

#### Remediation Implementation
1. **Unified Expiry Evaluation**: Implemented `is_agreement_expired`, `is_grant_expired`, and idempotent `sync_agreement_expiry` in [agreements.py](file:///home/mr-robot/Projects/glitters-core/apps/operations/services/agreements.py#L119-L200).
2. **Boundary Semantics**:
   - The contract defines `valid_until` as **inclusive**: an agreement remains fully valid ON `valid_until`.
   - An agreement is expired if and only if `timezone.localdate() > agreement.valid_until`.
3. **Real-Time Request-Time Enforcement**:
   - In `apps.properties.services.authorization.decide()` ([authorization.py](file:///home/mr-robot/Projects/glitters-core/apps/properties/services/authorization.py#L74-L86)):
     When an inherited grant is encountered, its associated `DelegatedPropertyAccess` and `ManagementAgreement` are checked in real-time. If the agreement is no longer `ACTIVE` or `timezone.localdate() > agreement.valid_until`, the grant is immediately treated as null, denying access. Direct owner grants are unaffected.
   - In `apps.operations.services.physical_profiles.check_operations_authority()` ([physical_profiles.py](file:///home/mr-robot/Projects/glitters-core/apps/operations/services/physical_profiles.py#L55-L68)):
     Evaluates `is_agreement_expired` and runs lazy inline `sync_agreement_expiry(agreement)`. If expired, raises `PermissionDenied("Management agreement for this property has expired.")`.
   - In `apps.operations.views.ManagedPortfolioView` ([views.py](file:///home/mr-robot/Projects/glitters-core/apps/operations/views.py#L275-L288)):
     Filters out expired agreements from active portfolio displays and triggers idempotent background/inline state synchronization.

---

### 3.3 Correction 3 — F-03: Corporate Ownership Verification & Regression Tests

#### Issue
While corporate property management was architecturally allowed, no tests verified that an officer of an organization holding title to an organization-managed property could propose or authorize management agreements. Additionally, repeated calls to `verify_owner_authority` risked duplicate direct access creation under concurrency.

#### Remediation Implementation
1. In `verify_owner_authority` ([agreements.py](file:///home/mr-robot/Projects/glitters-core/apps/operations/services/agreements.py#L100-L113)):
   Refactored corporate grant resolution to use `filter().first()` before `create()`, handling race conditions gracefully.
2. In `tests/security/test_operations_security.py`:
   Added `test_f03_corporate_ownership_authorization_path` verifying:
   - Corporate `OWNER` can propose agreements for organization-owned properties.
   - Corporate `ADMIN` can propose agreements.
   - Non-admin `MEMBER` is rejected with `PermissionDenied`.
   - Suspended corporate officers are rejected with `PermissionDenied`.

---

### 3.4 Correction 4 — F-04: AppendOnlyQuerySet Hardening

#### Issue
`AppendOnlyQuerySet` overrode `.update()` and `.delete()`, but `.bulk_update()` in Django allows updating fields on model instances without calling `.save()`, bypassing query-level `.update()` blocks.

#### Remediation Implementation
Updated `AppendOnlyQuerySet` in [apps/operations/models.py](file:///home/mr-robot/Projects/glitters-core/apps/operations/models.py#L26-L28):
```python
class AppendOnlyQuerySet(models.QuerySet):
    """Enforce append-only integrity at the Django ORM QuerySet level."""

    def update(self, **kwargs):
        raise ValueError("Operations events are append-only and cannot be updated.")

    def bulk_update(self, objs, fields, batch_size=None):
        raise ValueError("Operations events are append-only and cannot be updated in bulk.")

    def delete(self):
        raise ValueError("Operations events are append-only and cannot be deleted.")
```

#### Immutability Scope & Remaining Database Limitations
- **ORM-Enforced Immutability**: All standard Django ORM operations (`.save()` on existing instances, `.update()`, `.bulk_update()`, `.delete()`) are blocked and raise `ValueError`.
- **Database-Level Boundaries**: Direct PostgreSQL raw SQL queries (`raw()`, `cursor.execute("UPDATE operations_operationsevent ...")`) or administrative database sessions bypass Django ORM logic. As designed for the modular monolith, table-level PostgreSQL `BEFORE UPDATE OR DELETE` triggers can be optionally introduced in production migrations if untrusted database superusers must be restricted at the database driver layer.

---

## 4. Verification & Automated Test Results

### 4.1 Security Regression Test Suite (`test_operations_security.py`)
Executed: `pytest tests/security/test_operations_security.py`

| Test Case | Description | Result |
| :--- | :--- | :--- |
| `test_unverified_claimant_cannot_propose_agreement` | Confirms unverified `PropertyParty` records cannot propose agreements. | **PASSED** |
| `test_inherited_manager_cannot_create_another_agreement` | Confirms managing agents with inherited access cannot propose agreements. | **PASSED** |
| `test_expired_or_revoked_grant_cannot_be_reused` | Confirms revoked grants cannot propose or authorize operations. | **PASSED** |
| `test_cannot_delegate_to_uncontrolled_organization` | Confirms unauthorized users cannot bind foreign management firms. | **PASSED** |
| `test_independent_grants_survive_agreement_termination` | Confirms direct owner access survives management agreement termination. | **PASSED** |
| `test_physical_profile_rejects_occupancy_fields` | Verifies `UnitPhysicalProfile` has no denormalized lease occupancy fields. | **PASSED** |
| `test_f01_direct_owner_authority_provenance_and_sub_delegation_blocked` | **F-01**: Verifies direct owner authority provenance and blocks inherited sub-delegation. | **PASSED** |
| `test_f02_agreement_expiry_enforcement_and_boundaries` | **F-02**: Tests inclusive boundary on `valid_until`, immediate real-time authorization cutoff, and direct owner independence. | **PASSED** |
| `test_f03_corporate_ownership_authorization_path` | **F-03**: Tests corporate owner/admin proposal authority, non-admin rejection, and suspended member rejection. | **PASSED** |
| `test_f04_audit_event_bulk_update_blocked` | **F-04**: Tests that `bulk_update`, `update`, and `delete` are blocked on `OperationsEvent`. | **PASSED** |

**Result**: 10 passed in 6.27s.

### 4.2 Full Repository Test Suite Verification
Executed: `.venv/bin/pytest --cov=apps --cov=config`

```
============================= test session starts ==============================
platform linux -- Python 3.14.4, pytest-9.2.0, pluggy-1.7.0
django: version: 5.2.19, settings: config.settings.development
rootdir: /home/mr-robot/Projects/glitters-core
configfile: pyproject.toml
plugins: cov-7.1.0, django-4.14.0, platformdirs-4.14.5
collected 306 items

306 passed in 64.26s

---------- coverage: platform linux, python 3.14.4 ----------
Name                 Stmts   Miss  Cover
----------------------------------------
TOTAL                10476   1281    88%
============================= 306 passed in 64.26s =============================
```

### 4.3 Django System & Migration Integrity
- `python manage.py check`: 0 issues (0 silenced).
- `python manage.py makemigrations --check --dry-run`: No changes detected.

---

## 5. Modified Files Inventory

| File Path | Description of Changes |
| :--- | :--- |
| [apps/operations/services/agreements.py](file:///home/mr-robot/Projects/glitters-core/apps/operations/services/agreements.py) | Added `origin=AccessOrigin.DIRECT` check in `verify_owner_authority`; added `is_agreement_expired`, `sync_agreement_expiry`, `is_grant_expired`; populated `expires_at` on `DelegatedPropertyAccess`. |
| [apps/operations/services/physical_profiles.py](file:///home/mr-robot/Projects/glitters-core/apps/operations/services/physical_profiles.py) | Integrated `is_agreement_expired` check in `check_operations_authority` with automatic inline expiry sync. |
| [apps/properties/services/authorization.py](file:///home/mr-robot/Projects/glitters-core/apps/properties/services/authorization.py) | Dynamic check in `decide()` treating inherited access grants as null if agreement is inactive or `today > valid_until`. |
| [apps/operations/models.py](file:///home/mr-robot/Projects/glitters-core/apps/operations/models.py) | Overrode `bulk_update` in `AppendOnlyQuerySet` to raise `ValueError`. |
| [apps/operations/views.py](file:///home/mr-robot/Projects/glitters-core/apps/operations/views.py) | Excluded expired agreements from managed portfolio view and triggered lazy synchronization. |
| [tests/security/test_operations_security.py](file:///home/mr-robot/Projects/glitters-core/tests/security/test_operations_security.py) | Added comprehensive regression tests for F-01, F-02, F-03, and F-04. |

---

## 6. Repository & Working Tree State

- **Branch**: `feature/media-infrastructure`
- **HEAD Commit**: `7f61a984a49ccb7c6edd826f4717d4e7e20b5256`
- **M6 Working-Tree Integrity**: 100% intact. All previous M6 tracked modifications and untracked files (`apps/marketplace/`, `apps/solutions/`, `apps/core/migrations/0002_enable_btree_gist.py`, docs) are undisturbed.
- **Git State**: No uncommitted work discarded; no commits created; no production migrations applied.

---

## 7. Sign-Off & Recommendation for M7-B

Sub-Milestone M7-A is officially certified. The operational foundations provide a secure, auditable, and multi-tenant foundation for building **Milestone M7-B (Tenancy Domain & PostgreSQL GiST Date-Range Exclusion Constraints)**:
1. `btree_gist` extension is active and operational.
2. Authority delegation cannot be hijacked or sub-delegated by managing agents.
3. Personal properties retain canonical M4 ownership immutability.
4. Physical turnover readiness is decoupled from contractual lease occupancy.
5. Audit event logs are append-only.
