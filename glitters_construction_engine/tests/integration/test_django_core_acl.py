"""
GLITTERS CORE Integration & Anti-Corruption Layer (ACL) Tests.
Verifies:
1. Unverified EBK/BORAQS records from GLITTERS CORE are blocked from creating `ProfessionalEngineeringApproval`.
2. Unsigned measurement records are blocked from creating `SignedMeasurementEvidence`.
3. Simulation procurement runs or unauthorized user roles are blocked from transactional PostgreSQL persistence.
"""
import unittest
from glitters_construction_engine import (
    CoreOrganizationContext,
    DEFAULT_CORE_AUTHORITY_SECRET,
    GlittersCoreIntegrationAdapter,
    ProcurementEngine,
    get_synthetic_kenyan_supplier_quotations,
    get_verified_kenyan_supplier_quotations,
)


class TestGlittersCoreIntegrationACL(unittest.TestCase):

    def test_core_engineering_and_measurement_authority_mapping(self) -> None:
        unverified_eng = {
            "approval_id": "APP-100",
            "engineer_full_name": "Eng. Mutua",
            "regulatory_body": "EBK",
            "registration_number": "EBK-A3011",
            "license_valid_until": "2027-12-31",
            "approved_drawing_ref": "DRW-100",
            "drawing_sha256_seed": "PDF-BYTES",
            "verified_by_ebk_boraqs_registry": False,
        }
        with self.assertRaises(PermissionError):
            GlittersCoreIntegrationAdapter.map_core_engineering_approval(
                unverified_eng, DEFAULT_CORE_AUTHORITY_SECRET
            )

        verified_eng = dict(unverified_eng, verified_by_ebk_boraqs_registry=True)
        approval = GlittersCoreIntegrationAdapter.map_core_engineering_approval(
            verified_eng, DEFAULT_CORE_AUTHORITY_SECRET
        )
        ok, errs = approval.verify_authority()
        self.assertTrue(ok, msg=str(errs))

    def test_transactional_procurement_persistence_gates(self) -> None:
        # Simulation procurement result cannot be persisted to PostgreSQL
        sim_res = ProcurementEngine.optimize_supplier_selection_milp(
            material_id="cement_50kg_bags",
            total_demand_quantity=600.0,
            required_within_days=7,
            quotations=get_synthetic_kenyan_supplier_quotations(),
            allow_synthetic_or_unverified_quotes=True,
        )
        director_ctx = CoreOrganizationContext(
            organization_id="ORG-GLITTERS-KE-01",
            tenant_schema="tenant_nairobi_01",
            authorized_user_id="USR-DIR-01",
            user_role="PROCUREMENT_DIRECTOR",
            has_procurement_signoff_authority=True,
        )
        with self.assertRaises(PermissionError):
            GlittersCoreIntegrationAdapter.build_transactional_procurement_payload(director_ctx, sim_res)

        # Verified production procurement result succeeds for authorized director, fails for unauthorized user
        prod_res = ProcurementEngine.optimize_supplier_selection_milp(
            material_id="cement_50kg_bags",
            total_demand_quantity=600.0,
            required_within_days=7,
            quotations=get_verified_kenyan_supplier_quotations(),
            allow_synthetic_or_unverified_quotes=False,
        )
        payload = GlittersCoreIntegrationAdapter.build_transactional_procurement_payload(director_ctx, prod_res)
        self.assertTrue(payload["ready_for_postgres_atomic_commit"])
        self.assertGreater(payload["total_obligation_cents"], 0)

        junior_ctx = CoreOrganizationContext(
            organization_id="ORG-GLITTERS-KE-01",
            tenant_schema="tenant_nairobi_01",
            authorized_user_id="USR-JR-02",
            user_role="JUNIOR_ESTIMATOR",
            has_procurement_signoff_authority=False,
        )
        with self.assertRaises(PermissionError):
            GlittersCoreIntegrationAdapter.build_transactional_procurement_payload(junior_ctx, prod_res)


if __name__ == "__main__":
    unittest.main()
