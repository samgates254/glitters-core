# GLITTERS CORE — Milestone M7-B Phase 2A Remediation Report: Architectural Integrity Remediation

**Document Path**: `docs/engineering/M7_B_PHASE2A_REMEDIATION_REPORT.md`
**Milestone**: Milestone 7-B (Tenancy Domain & Temporal Occupancy Engine — Phase 2A Integrity Remediation)
**Status**: COMPLETE & VERIFIED
**Date**: November 12, 2026
**Author**: Principal Software Architect & Senior Backend Engineer
**Repository**: `glitters-core` ([/home/mr-robot/Projects/glitters-core](file:///home/mr-robot/Projects/glitters-core))
**Companion Documents**:
- [M7_B_APPROVED_ARCHITECTURE.md](file:///home/mr-robot/Projects/glitters-core/docs/engineering/M7_B_APPROVED_ARCHITECTURE.md)
- [M7_B_PHASE1_FINAL_VERIFICATION.md](file:///home/mr-robot/Projects/glitters-core/docs/engineering/M7_B_PHASE1_FINAL_VERIFICATION.md)
- [M7_B_PHASE1_COMPLETION_REPORT.md](file:///home/mr-robot/Projects/glitters-core/docs/engineering/M7_B_PHASE1_COMPLETION_REPORT.md)

---

## 1. Executive Summary

Milestone **M7-B Phase 2A (Architectural Integrity Remediation)** has resolved all four foundational integrity requirements identified during the Phase 1 Final Adversarial Verification before beginning Phase 2B domain services and temporal state machine implementation.

1. **Management Agreement Authority Invariant**:
   - `can_execute_leases` is established as an explicit bilateral contractual power of attorney granted by the property owner, not an operational setting.
   - Wired into `propose_agreement()`, `ManagementAgreementSerializer`, and `ManagementAgreementCreateSerializer`.
   - Prevented silent mutation after acceptance/activation via `ManagementAgreement.save()` and `ManagementAgreementQuerySet.update()`.
   - Implemented explicit owner-only revocation via `revoke_lease_execution_authority()`.
   - Implemented authority scoping and distinction between ordinary managing agents (`can_execute_leases=False`) and mandated agents (`can_execute_leases=True`) via `verify_lease_execution_authority()`.
2. **Canonical Rentable-Unit Validation**:
   - Implemented reusable domain validation in `apps.tenancy.services.validation::validate_rentable_unit` and integrated into `Lease.clean()`.
   - Enforces that only canonical rentable resources (`UNIT` or `STANDALONE`) can be leased.
   - Rejects aggregate containers (`BUILDING`, `ESTATE`) and properties with subdivided child units to eliminate ambiguous parent/child occupancy allocation.
3. **Executed Lease & Party Immutability**:
   - Implemented strict immutability locks on core contractual terms (`IMMUTABLE_CONTRACT_FIELDS`) across instance `.save()`, QuerySet `.update()`, `.bulk_update()`, and `.delete()`.
   - Protected `LeaseParty` records against mutation or deletion once a lease reaches legal execution (`EXECUTED = {FULLY_EXECUTED, ACTIVE, UNDER_RENEWAL, TERMINATED_NORMAL, TERMINATED_EARLY}`).
   - Registered `apps.tenancy.admin` with read-only field locks on executed agreements.
   - Guaranteed that early termination updates lifecycle status (`TERMINATED_EARLY`) and adjusts `effective_end_date` without rewriting the original signed contractual terms (`term_end_date`, `start_date`, `rent_amount_minor`, document hashes).
4. **Signature Evidence Uniqueness & Non-Signatory Prohibition**:
   - Added PostgreSQL `UniqueConstraint` on `("lease_id", "party_id", "document_version")` preventing duplicate signature submissions for the same party and version while permitting legitimate re-signing upon document version increments.
   - Added PostgreSQL `CheckConstraint` on `NOT (signatory_role = 'authorized_occupant')` preventing non-signatory occupants from submitting signature evidence.
5. **Verification & Repository Safety**:
   - Full repository test suite passes with **337 passed tests** (0 failed, 0 errors) in 69.20s.
   - Generated migration `tenancy.0002_signature_evidence_constraints` inspected and validated via `sqlmigrate`.
   - No Git commits created; working tree preserved.

---

## 2. Findings Addressed

| Finding / Area | Severity | Root Cause in Phase 1 | Phase 2A Resolution | Status |
| :--- | :--- | :--- | :--- | :--- |
| **Area 1: Management Agreement Authority** | Medium | `can_execute_leases` defaulted to `False` but was missing from proposal API, serializers, and lacked immutability locks and owner revocation service. | Wired into `propose_agreement()`, serializers, and views; added ORM/QuerySet locks; added `revoke_lease_execution_authority()` and `verify_lease_execution_authority()`. | **RESOLVED** |
| **Area 2: Canonical Rentable-Unit Validation** | Medium | ORM `clean()` checked `hierarchy_role` but lacked reusable validation against aggregate parents, orphan units, and subdivided children. | Implemented `validate_rentable_unit()`; rejects aggregate buildings/estates, orphan units, and parent properties with child units. Integrated into `Lease.clean()`. | **RESOLVED** |
| **Area 3: Executed Lease Immutability** | Medium | `Lease` instance `.save()`, `.update()`, and related parties permitted silent ORM modifications after contract execution. | Added `IMMUTABLE_CONTRACT_FIELDS`, custom `LeaseQuerySet` and `LeasePartyQuerySet` blocking updates/deletes, instance `save()` guards, and admin read-only locks. | **RESOLVED** |
| **Area 4: Signature Evidence Uniqueness** | Low | `LeaseSignatureEvidence` lacked a composite uniqueness constraint on `(lease, party, document_version)`. | Added PostgreSQL `UniqueConstraint` and `CheckConstraint` forbidding occupant signatures via migration `tenancy.0002`. | **RESOLVED** |

---

## 3. Exact Files Modified and Created

### Modified Existing Files

1. [`apps/operations/models.py`](file:///home/mr-robot/Projects/glitters-core/apps/operations/models.py):
   - Added `ManagementAgreementQuerySet` overriding `.update()` to block bulk mutation of `can_execute_leases` on non-draft agreements.
   - Added `ManagementAgreement.save()` guard preventing changes to `can_execute_leases` on accepted/active agreements unless `_allow_authority_revocation` is explicitly set.
2. [`apps/operations/services/agreements.py`](file:///home/mr-robot/Projects/glitters-core/apps/operations/services/agreements.py):
   - Added `can_execute_leases: bool = False` parameter to `propose_agreement()`.
   - Implemented `revoke_lease_execution_authority()` (owner-only service).
   - Implemented `verify_lease_execution_authority()` verifying inherent owner authority or active agreement with `can_execute_leases=True`.
   - Updated `terminate_agreement()` to disable `can_execute_leases` upon termination.
3. [`apps/operations/serializers.py`](file:///home/mr-robot/Projects/glitters-core/apps/operations/serializers.py):
   - Added `can_execute_leases` to `ManagementAgreementSerializer` and `ManagementAgreementCreateSerializer`.
4. [`apps/operations/views.py`](file:///home/mr-robot/Projects/glitters-core/apps/operations/views.py):
   - Passed `can_execute_leases` in `ManagementAgreementListCreateView.post`.
   - Added `ManagementAgreementRevokeAuthorityView`.
5. [`apps/operations/urls.py`](file:///home/mr-robot/Projects/glitters-core/apps/operations/urls.py):
   - Registered route `agreements/<uuid:pk>/revoke-authority/`.
6. [`apps/operations/admin.py`](file:///home/mr-robot/Projects/glitters-core/apps/operations/admin.py):
   - Added `get_readonly_fields` to lock `can_execute_leases` and core terms on accepted/active agreements.
7. [`apps/tenancy/domain.py`](file:///home/mr-robot/Projects/glitters-core/apps/tenancy/domain.py):
   - Added `LeaseStatus.EXECUTED = frozenset({FULLY_EXECUTED, ACTIVE, UNDER_RENEWAL, TERMINATED_NORMAL, TERMINATED_EARLY})`.
8. [`apps/tenancy/models.py`](file:///home/mr-robot/Projects/glitters-core/apps/tenancy/models.py):
   - Added `IMMUTABLE_CONTRACT_FIELDS` constant.
   - Added `LeaseQuerySet` overriding `.update()`, `.bulk_update()`, `.delete()`.
   - Added `Lease.save()` and `Lease.delete()` immutability guards.
   - Added `LeasePartyQuerySet`, `LeaseParty.save()`, and `LeaseParty.delete()` immutability guards.
   - Added `UniqueConstraint` on `("lease", "party", "document_version")` to `LeaseSignatureEvidence`.
   - Added `CheckConstraint` forbidding `signatory_role == "authorized_occupant"`.
   - Added `clean()` and `save()` append-only validations on `LeaseSignatureEvidence`.

### Created New Files

1. [`apps/tenancy/services/validation.py`](file:///home/mr-robot/Projects/glitters-core/apps/tenancy/services/validation.py):
   - Reusable domain validation service `validate_rentable_unit(property_record: Property)`.
2. [`apps/tenancy/services/__init__.py`](file:///home/mr-robot/Projects/glitters-core/apps/tenancy/services/__init__.py):
   - Service export module.
3. [`apps/tenancy/admin.py`](file:///home/mr-robot/Projects/glitters-core/apps/tenancy/admin.py):
   - Django admin configuration with immutability locks on executed leases and parties.
4. [`apps/tenancy/migrations/0002_signature_evidence_constraints.py`](file:///home/mr-robot/Projects/glitters-core/apps/tenancy/migrations/0002_signature_evidence_constraints.py):
   - Schema migration introducing PostgreSQL `UniqueConstraint` and `CheckConstraint` on `tenancy_signature_evidence`.
5. [`tests/integration/test_m7b_phase2a_remediation.py`](file:///home/mr-robot/Projects/glitters-core/tests/integration/test_m7b_phase2a_remediation.py):
   - 12 comprehensive integration tests covering all 4 remediation areas.

---

## 4. Migration Names and Dependencies

### Migration: `apps/tenancy/migrations/0002_signature_evidence_constraints.py`

- **App**: `tenancy`
- **Dependencies**: `("tenancy", "0001_initial")`
- **Operations**:
  1. `Create constraint tenancy_sig_evidence_no_occupant on model leasesignatureevidence`
  2. `Create constraint tenancy_sig_evidence_unique_party_version on model leasesignatureevidence`

#### Compiled SQL DDL (`python manage.py sqlmigrate tenancy 0002`)
```sql
BEGIN;
--
-- Create constraint tenancy_sig_evidence_no_occupant on model leasesignatureevidence
--
ALTER TABLE "tenancy_signature_evidence"
ADD CONSTRAINT "tenancy_sig_evidence_no_occupant"
CHECK (NOT ("signatory_role" = 'authorized_occupant'));

--
-- Create constraint tenancy_sig_evidence_unique_party_version on model leasesignatureevidence
--
ALTER TABLE "tenancy_signature_evidence"
ADD CONSTRAINT "tenancy_sig_evidence_unique_party_version"
UNIQUE ("lease_id", "party_id", "document_version");

COMMIT;
```

---

## 5. Test Names and Actual Execution Results

### 5.1 Targeted Remediation Suite (`tests/integration/test_m7b_phase2a_remediation.py`)

| Test Name | Focus Area | Result |
| :--- | :--- | :--- |
| `test_mandated_managing_agent_possesses_lease_execution_authority` | Agreement Authority: Proposal, acceptance, activation, mandated agent authority | **PASSED** |
| `test_ordinary_agent_without_mandate_lacks_lease_execution_authority` | Agreement Authority: Ordinary agent without `can_execute_leases` blocked (403) | **PASSED** |
| `test_unauthorized_mutation_of_can_execute_leases_is_blocked` | Agreement Authority: Mutation blocked via instance save and QuerySet update | **PASSED** |
| `test_agent_cannot_grant_themselves_authority_unilaterally` | Agreement Authority: Agent proposing authority blocked from activating or accepting | **PASSED** |
| `test_owner_explicit_revocation_of_lease_execution_authority` | Agreement Authority: Owner revokes authority; management access preserved; agent blocked | **PASSED** |
| `test_agreement_termination_revokes_lease_execution_authority` | Agreement Authority: Termination clears authority and revokes all delegated access | **PASSED** |
| `test_lease_execution_authority_scoped_strictly_to_agreement_property` | Agreement Authority: Mandate on Property A strictly rejected on Property B | **PASSED** |
| `test_canonical_rentable_unit_validation_paths` | Unit Validation: Standalone, child unit, aggregate building, estate, subdivided, orphan | **PASSED** |
| `test_executed_lease_immutability_guards` | Lease Immutability: Rent/date mutation blocked on save, update, bulk_update, delete | **PASSED** |
| `test_early_termination_preserves_contractual_history` | Lease Immutability: Early termination modifies lifecycle without corrupting terms | **PASSED** |
| `test_signature_evidence_unique_party_version_constraint` | Signature Uniqueness: Duplicate rejected by DB constraint; re-signing v2 succeeds | **PASSED** |
| `test_authorized_occupant_cannot_sign_evidence` | Occupant Restriction: Clean validation and DB check constraint block occupant | **PASSED** |

### 5.2 Full Repository Regression Suite
```
============================= test session starts ==============================
platform linux -- Python 3.14.4, pytest-9.2.0, pluggy-1.7.0
django: version: 5.2.19, settings: config.settings.development (from ini)
rootdir: /home/mr-robot/Projects/glitters-core
configfile: pyproject.toml
testpaths: tests
plugins: cov-7.1.0, django-4.14.0, platformdirs-4.14.5
collected 337 items

tests/integration/test_enquiry_inbox.py ............                     [  3%]
tests/integration/test_enquiry_lifecycle.py ...........                  [  6%]
tests/integration/test_grant_provenance.py ..                            [  7%]
tests/integration/test_health.py ...                                     [  8%]
tests/integration/test_identity_api.py .......................           [ 15%]
tests/integration/test_locations_api.py ....                             [ 16%]
tests/integration/test_m7b_phase2a_remediation.py ............           [ 19%]
tests/integration/test_marketplace_api.py ........                       [ 22%]
tests/integration/test_marketplace_authorization.py .......              [ 24%]
tests/integration/test_media_uploads.py ..........                       [ 27%]
tests/integration/test_operations_api.py ....                            [ 28%]
tests/integration/test_organizations_api.py ................             [ 33%]
tests/integration/test_properties_api.py .....                           [ 34%]
tests/integration/test_public_enquiries.py .........................     [ 42%]
tests/integration/test_solutions_workflows.py .......                    [ 44%]
tests/integration/test_tenancy_models.py .............                   [ 48%]
tests/security/test_baseline.py ..                                       [ 48%]
tests/security/test_identity_security.py .....                           [ 50%]
tests/security/test_invitation_reactivation.py ....                      [ 51%]
tests/security/test_marketplace_privacy.py ....                          [ 52%]
tests/security/test_operations_security.py ..........                    [ 55%]
tests/security/test_organizations_security.py ......                     [ 57%]
tests/security/test_properties_security.py .............                 [ 61%]
tests/unit/test_enquiry_access.py ....                                   [ 62%]
tests/unit/test_enquiry_persistence.py ........                          [ 64%]
tests/unit/test_identity.py ........                                     [ 67%]
tests/unit/test_identity_domain.py ......                                [ 68%]
tests/unit/test_operations_domain.py ......                              [ 70%]
tests/unit/test_operations_services.py ..........                        [ 73%]
tests/unit/test_organizations_domain.py .                                [ 73%]
tests/unit/test_tenancy_domain.py ......                                 [ 75%]
tests/security/test_identity_security.py .                               [ 75%]
tests/security/test_organizations_security.py ..                         [ 76%]
tests/security/test_properties_security.py .                             [ 76%]
tests/integration/test_health.py .....                                   [ 78%]
tests/security/test_baseline.py ....                                     [ 79%]
tests/unit/test_api_errors.py .                                          [ 79%]
tests/unit/test_enquiry_input.py .....                                   [ 81%]
tests/unit/test_enquiry_persistence.py .............................     [ 89%]
tests/unit/test_identity.py .                                            [ 90%]
tests/unit/test_identity_domain.py .......                               [ 92%]
tests/unit/test_media_storage.py ......                                  [ 94%]
tests/unit/test_organizations_domain.py ...                              [ 94%]
tests/unit/test_property_rules.py ........                               [ 97%]
tests/unit/test_request_id.py ..                                         [ 97%]
tests/unit/test_solutions_domain.py .......                              [100%]

======================== 337 passed in 69.20s (0:01:09) ========================
```

---

## 6. Unresolved Issues & Remaining Architectural Risks

1. **Unresolved Issues**: None. All four remediation requirements from the Phase 1 Final Adversarial Verification have been implemented, tested, and verified against PostgreSQL.
2. **Remaining Architectural Risks**:
   - **Cross-Table Constraints**: PostgreSQL check constraints cannot contain subqueries across foreign keys (e.g. from `tenancy_lease` into `properties_property.hierarchy_role`). While `validate_rentable_unit` and `Lease.clean()` enforce unit eligibility at the application/ORM layer, direct SQL inserts by external processes would not be caught by a check constraint. To eliminate this risk, Phase 2B domain services (`create_draft_lease`) must always invoke `validate_rentable_unit`.
   - **Database Superuser Immutability**: Immutability for executed leases is enforced at the Django ORM level (instance `.save()`, QuerySet `.update()`, `.bulk_update()`, `.delete()`, and Django Admin). Direct raw SQL queries executed by a PostgreSQL superuser are not blocked by a database trigger. This follows standard platform convention across `glitters-core` audit models (`BaseAuditEvent`, `OperationsEvent`, `TenancyEvent`).

---

## 7. Git Status & Confirmation of No Commits

Inspection of `git status --short`:
```
 M apps/marketplace/domain.py
 M apps/marketplace/filters.py
 M apps/marketplace/models.py
 M apps/marketplace/serializers.py
 M apps/marketplace/services/access.py
 M apps/marketplace/services/representation.py
 M apps/marketplace/urls.py
 M apps/marketplace/views.py
 M apps/operations/admin.py
 M apps/operations/models.py
 M apps/operations/serializers.py
 M apps/operations/services/agreements.py
 M apps/operations/urls.py
 M apps/operations/views.py
 M apps/organizations/domain.py
 M apps/properties/services/authorization.py
 M apps/tenancy/domain.py
 M apps/tenancy/models.py
 M config/settings/base.py
 M config/urls.py
 M docs/api/V1.md
 M docs/architecture/RBAC_ARCHITECTURE.md
 M tests/conftest.py
 M tests/integration/test_organizations_api.py
 M tests/unit/test_organizations_domain.py
?? apps/core/migrations/0002_enable_btree_gist.py
?? apps/marketplace/migrations/0003_enquiry_enquiryevent_and_more.py
?? apps/marketplace/services/enquiries.py
?? apps/marketplace/throttles.py
?? apps/operations/
?? apps/solutions/
?? apps/tenancy/admin.py
?? apps/tenancy/migrations/0002_signature_evidence_constraints.py
?? apps/tenancy/services/
?? docs/engineering/ECOSYSTEM_TRACEABILITY.md
?? docs/engineering/M6_BASELINE_RECONCILIATION.md
?? docs/engineering/M7_APPROVED_ARCHITECTURE.md
?? docs/engineering/M7_ARCHITECTURE_CHALLENGE.md
?? docs/engineering/M7_A_COMPLETION_REPORT.md
?? docs/engineering/M7_A_SECURITY_REMEDIATION_REPORT.md
?? docs/engineering/M7_B_APPROVED_ARCHITECTURE.md
?? docs/engineering/M7_B_ARCHITECTURE_CHALLENGE.md
?? docs/engineering/M7_B_IMPLEMENTATION_PLAN.md
?? docs/engineering/M7_B_PHASE1_COMPLETION_REPORT.md
?? docs/engineering/M7_B_PHASE1_FINAL_VERIFICATION.md
?? docs/engineering/M7_B_PHASE2A_REMEDIATION_REPORT.md
?? docs/engineering/M7_IMPLEMENTATION_PLAN.md
?? docs/engineering/M7_PROPERTY_MANAGEMENT_ARCHITECTURE.md
?? docs/engineering/SOLUTIONS_READINESS.md
?? tests/integration/test_enquiry_inbox.py
?? tests/integration/test_enquiry_lifecycle.py
?? tests/integration/test_m7b_phase2a_remediation.py
?? tests/integration/test_operations_api.py
?? tests/integration/test_public_enquiries.py
?? tests/integration/test_solutions_workflows.py
?? tests/integration/test_tenancy_models.py
?? tests/security/test_operations_security.py
?? tests/unit/test_enquiry_access.py
?? tests/unit/test_enquiry_input.py
?? tests/unit/test_enquiry_persistence.py
?? tests/unit/test_operations_domain.py
?? tests/unit/test_operations_services.py
?? tests/unit/test_solutions_domain.py
?? tests/unit/test_tenancy_domain.py
```

**Confirmation**:
- Current branch: `feature/media-infrastructure` (HEAD: `7f61a984a49ccb7c6edd826f4717d4e7e20b5256`).
- **Zero Git commits created.**
- No Git stash, reset, or checkout discards performed.
- All M6, M7-A, and M7-B Phase 1 modifications remain intact.

---

## 8. Stop Condition & Readiness

Milestone **M7-B Phase 2A is complete**. As instructed, implementation has stopped prior to domain services, the lease state machine, the signature hashing engine, API endpoints, and frontend integration.

**Awaiting architectural review and explicit authorization for Phase 2B.**
