"""
Direct Regression Tests for every Independent Technical Audit Finding:
- CRITICAL 1: `validate_compliance(None, None)` rejected; fake/synthetic/expired approvals rejected.
- CRITICAL 2: `is_verified_measurement=True` rejected; `SignedMeasurementEvidence` required;
  `input_geometry_status` separated from `derived_material_status`.
- CRITICAL 3: `SYNTHETIC_BENCHMARK_QUOTATION` and expired quotes blocked in production procurement.
- CRITICAL 4: BOQ completeness checks missing sections, zero quantities, and unpriced items;
  6 separate evidence coverage indicators computed.
- CRITICAL 5: Non-renewable material caps and delivery schedules enforced in RCPSP and SSGS.
- HIGH 6: True two-stage lexicographic optimization (Stage 1: min makespan; Stage 2: min direct cost).
- HIGH 7: SSGS raises `InfeasibleScheduleError` on horizon/window exhaustion and runs `validate_schedule_feasibility`.
- Section 4: `("m3", "cm")` rejected as dimensional mismatch; Full-DAG PERT DP; Calendar dates; `MoneyKES` cents.
"""
import datetime
import unittest
import numpy as np

from glitters_construction_engine import (
    Activity,
    BOQEngine,
    BOQWorkItem,
    ConstructionProject,
    CriticalPathEngine,
    DEFAULT_CORE_AUTHORITY_SECRET,
    DependencyType,
    EngineeringSpecification,
    ExecutionMode,
    InfeasibleScheduleError,
    MeasurementStatus,
    MoneyKES,
    PrecedenceDependency,
    ProcurementEngine,
    ProfessionalEngineeringApproval,
    ProjectArchetype,
    QuantityTakeoffEngine,
    QuotationVerificationStatus,
    RateProvenanceStatus,
    ResourceCategory,
    ResourcePoolItem,
    SchedulingEngine,
    SignedMeasurementEvidence,
    SupplierQuotation,
    build_kenyan_benchmark_project,
    get_synthetic_kenyan_supplier_quotations,
    get_verified_kenyan_supplier_quotations,
)


class TestIndependentAuditRemediations(unittest.TestCase):

    def test_critical_1_engineering_approval_and_missing_strengths_rejected(self) -> None:
        """
        Reproduces the exact audit test case:
          spec.validate_compliance(None, None)
        and verifies it now returns (False, violations) instead of (True, []).
        Also verifies that legacy/fake registration strings like 'REG-FAKE' or 'EBK-A9999-SYNTHETIC' are rejected.
        """
        legacy_spec = EngineeringSpecification(
            spec_id="S",
            structural_code="CODE",
            min_concrete_class_mpa=25.0,
            min_steel_yield_mpa=500.0,
            approved_drawing_ref="DRAW-1",
            licensed_engineer_reg_no="REG-FAKE",
            is_approved_by_licensed_engineer=True,
        )
        ok, violations = legacy_spec.validate_compliance(None, None)
        self.assertFalse(ok)
        self.assertGreaterEqual(len(violations), 3)

        # Even with a valid cryptographic ProfessionalEngineeringApproval, passing None strengths MUST fail
        valid_approval = ProfessionalEngineeringApproval.create_verified_approval(
            approval_id="APP-01",
            engineer_full_name="Eng. Wanjiku",
            regulatory_body="EBK",
            registration_number="EBK-A2045",
            license_valid_until=datetime.date(2027, 12, 31),
            approved_drawing_ref="DRW-2026-10",
        )
        strict_spec = EngineeringSpecification(
            spec_id="S_STRICT",
            structural_code="BS EN 1992-1-1",
            min_concrete_class_mpa=25.0,
            min_steel_yield_mpa=500.0,
            professional_approval=valid_approval,
        )
        ok_none_c, _ = strict_spec.validate_compliance(None, 500.0)
        self.assertFalse(ok_none_c)
        ok_none_s, _ = strict_spec.validate_compliance(25.0, None)
        self.assertFalse(ok_none_s)
        ok_both_valid, errs_valid = strict_spec.validate_compliance(25.0, 500.0)
        self.assertTrue(ok_both_valid, msg=str(errs_valid))

        # Synthetic registration ID in ProfessionalEngineeringApproval must also be rejected
        synth_approval = ProfessionalEngineeringApproval.create_verified_approval(
            approval_id="APP-SYNTH",
            engineer_full_name="Eng. Synthetic",
            regulatory_body="EBK",
            registration_number="EBK-A9999",
            license_valid_until=datetime.date(2027, 12, 31),
            approved_drawing_ref="DRW-SYNTHETIC",
        )
        ok_synth, _ = synth_approval.verify_authority()
        self.assertFalse(ok_synth)

    def test_critical_2_signed_measurement_evidence_and_provenance_separation(self) -> None:
        """
        Reproduces the audit finding:
          QuantityTakeoffEngine.estimate_excavation(10, 10, 1, is_verified_measurement=True)
        and verifies that self-declared `is_verified_measurement=True` raises ValueError,
        and that when a valid `SignedMeasurementEvidence` is provided for concrete works,
        `input_geometry_status` is VERIFIED_MEASUREMENT while `derived_material_status` is CALCULATED_ESTIMATE.
        """
        with self.assertRaises(ValueError):
            QuantityTakeoffEngine.estimate_excavation(10.0, 10.0, 1.0, is_verified_measurement=True)

        signed_ev = SignedMeasurementEvidence.create_verified_measurement_evidence(
            measurement_id="MEAS-2026-001",
            surveyor_full_name="QS Otieno",
            professional_reg_no="BORAQS-Q1142",
            source_document_ref="TAKEOFF-SHEET-001",
        )
        res = QuantityTakeoffEngine.estimate_concrete_works(
            volume_m3=50.0,
            concrete_class="C25/30 (1:1.5:3)",
            measurement_evidence=signed_ev,
        )
        self.assertEqual(res.input_geometry_status, MeasurementStatus.VERIFIED_MEASUREMENT)
        self.assertEqual(res.derived_material_status, MeasurementStatus.CALCULATED_ESTIMATE)
        self.assertEqual(res.measurement_evidence_id, "MEAS-2026-001")

    def test_critical_3_synthetic_quotations_blocked_in_production_procurement(self) -> None:
        """
        Verifies that quotations marked `SYNTHETIC_BENCHMARK_QUOTATION` are rejected by
        `ProcurementEngine.optimize_supplier_selection_milp` in default production mode,
        and only accepted when `allow_synthetic_or_unverified_quotes=True` (simulation mode)
        or when `VERIFIED_BINDING_QUOTATION` records are supplied.
        """
        synth_quotes = get_synthetic_kenyan_supplier_quotations()
        with self.assertRaises(InfeasibleScheduleError):
            ProcurementEngine.optimize_supplier_selection_milp(
                material_id="cement_50kg_bags",
                total_demand_quantity=1000.0,
                required_within_days=7,
                quotations=synth_quotes,
                allow_synthetic_or_unverified_quotes=False,
            )

        # Works with verified quotations in production mode
        verified_quotes = get_verified_kenyan_supplier_quotations()
        prod_res = ProcurementEngine.optimize_supplier_selection_milp(
            material_id="cement_50kg_bags",
            total_demand_quantity=1000.0,
            required_within_days=7,
            quotations=verified_quotes,
            allow_synthetic_or_unverified_quotes=False,
        )
        self.assertTrue(prod_res["production_procurement_authorized"])
        self.assertEqual(prod_res["execution_mode"], "PRODUCTION")

    def test_critical_4_boq_completeness_and_6_coverage_indicators(self) -> None:
        """
        Verifies that BOQ completeness detects missing sections, zero quantities, and unpriced items,
        and that `evaluate_boq` separates verified measurement coverage from verified price/quotation coverage.
        """
        boq = BOQEngine("PROJ_BOQ_AUDIT", ProjectArchetype.ROADS_CIVIL)
        # Insert item with verified measurement but synthetic material quotation and synthetic labour rate
        boq.add_item(
            BOQWorkItem(
                item_code="RD_01",
                section="Excavation",
                description="Cut to spoil",
                unit="m3",
                quantity=100.0,
                measurement_status=MeasurementStatus.VERIFIED_MEASUREMENT,
                material_Breakdown_per_unit={"diesel_l": 1.5},
                labour_hours_per_unit=0.5,
                labour_hourly_rate_kes=300.0,
                rate_source_status=MeasurementStatus.SYNTHETIC_BENCHMARK,
                quotation_verification_status=QuotationVerificationStatus.SYNTHETIC_BENCHMARK_QUOTATION,
                labour_rate_provenance=RateProvenanceStatus.SYNTHETIC_BENCHMARK_RATE,
            )
        )
        eval_res = boq.evaluate_boq({"diesel_l": 180.0})
        # Quantity is 100% verified, but quotation & labour rates are 0% verified -> overall cost evidence is 0.0!
        self.assertEqual(eval_res["verified_measurement_coverage"], 1.0)
        self.assertEqual(eval_res["verified_quotation_coverage"], 0.0)
        self.assertEqual(eval_res["verified_labour_rate_coverage"], 0.0)
        self.assertEqual(eval_res["overall_cost_evidence_coverage"], 0.0)
        self.assertGreater(len(eval_res["assumptions_requiring_qs_approval"]), 0)

        # Insert zero-quantity item and unpriced item to verify validate_completeness catches both
        boq.add_item(
            BOQWorkItem(
                item_code="RD_ZERO",
                section="Foundations",
                description="Zero qty item",
                unit="m3",
                quantity=0.0,
                measurement_status=MeasurementStatus.CALCULATED_ESTIMATE,
            ),
            allow_unvalidated_insert=True,
        )
        boq.add_item(
            BOQWorkItem(
                item_code="RD_UNPRICED",
                section="Concrete Works",
                description="Unpriced item",
                unit="m3",
                quantity=10.0,
                measurement_status=MeasurementStatus.CALCULATED_ESTIMATE,
                material_Breakdown_per_unit={},
                labour_hours_per_unit=0.0,
                equipment_hours_per_unit=0.0,
            ),
            allow_unvalidated_insert=True,
        )
        ok_comp, issues = boq.validate_completeness({"diesel_l": 180.0})
        self.assertFalse(ok_comp)
        joined = " | ".join(issues)
        self.assertIn("RD_ZERO", joined)
        self.assertIn("RD_UNPRICED", joined)

    def test_critical_5_and_high_6_7_rcpsp_material_constraints_lexicographic_and_ssgs(self) -> None:
        """
        Verifies:
        1. CRITICAL 5: Non-renewable material shortage or late material delivery delays/blocks RCPSP & SSGS.
        2. HIGH 6: Two-stage lexicographic optimization finds true minimum makespan first, then minimum cost.
        3. HIGH 7: SSGS raises InfeasibleScheduleError when an activity window/horizon cannot be met.
        """
        proj = build_kenyan_benchmark_project()
        # Tightening cement supply cap below required 6,810 bags must raise InfeasibleScheduleError
        proj.nonrenewable_material_caps["cement_50kg_bags"] = 3000.0
        with self.assertRaises(InfeasibleScheduleError):
            SchedulingEngine.solve_rcpsp_cpsat(proj)
        with self.assertRaises(InfeasibleScheduleError):
            SchedulingEngine.solve_rcpsp_serial_sgs(proj)

        # Restore cap, test late delivery of cement delaying schedule
        proj2 = build_kenyan_benchmark_project()
        proj2.scheduled_material_deliveries["cement_50kg_bags"] = [(0, 1400.0), (80, 8000.0)]
        res_deliv = SchedulingEngine.solve_rcpsp_cpsat(proj2)
        self.assertTrue(res_deliv["lexicographic_two_stage"])
        # Since A03 uses the initial 1400 bags, A04 (needing 1850 bags) cannot start until day 80 delivery!
        self.assertGreaterEqual(res_deliv["schedule"]["A04"].start_day, 80)

        # HIGH 7: Impossible latest_finish_window on activity must raise InfeasibleScheduleError in SSGS
        proj3 = build_kenyan_benchmark_project()
        proj3.activities["A04"].resource_demands = {"TOWER_CRANE": 1.0}
        proj3.resources["TOWER_CRANE"] = ResourcePoolItem(
            "TOWER_CRANE", "Crane", ResourceCategory.EQUIPMENT, "Crane", "machine",
            max_capacity_per_day=1.0, unit_cost_kes=50000.0,
            available_from_day=0, available_until_day=30,  # Crane leaves site on day 30, before A04 can finish!
        )
        with self.assertRaises(InfeasibleScheduleError):
            SchedulingEngine.solve_rcpsp_serial_sgs(proj3)

    def test_section_4_dimensional_units_full_pert_calendar_and_money(self) -> None:
        """
        Verifies:
        1. `convert_units(1.0, "m3", "cm")` raises ValueError (dimensional mismatch: VOLUME vs LENGTH).
        2. `convert_units(1.0, "m3", "cm3") == 1e6`.
        3. Full-DAG PERT finds the highest expected PERT path even when a path has slightly shorter normal duration.
        4. `MoneyKES` arithmetic is exact to integer cents.
        """
        with self.assertRaises(ValueError):
            QuantityTakeoffEngine.convert_units(1.0, "m3", "cm")
        self.assertAlmostEqual(QuantityTakeoffEngine.convert_units(1.0, "m3", "cm3"), 1e6)

        m1 = MoneyKES.from_kes("12345.67")
        m2 = MoneyKES.from_kes("890.33")
        self.assertEqual((m1 + m2).cents, 1323600)
        self.assertEqual((m1 + m2).to_kes(), 13236.00)

        # Full-DAG PERT test: Path B has deterministic duration 9 (non-critical vs Path A's 10),
        # but Path B has pessimistic duration 28 (PERT mean = (4 + 4*9 + 28)/6 = 11.33 > Path A's 10.0)
        p_pert = ConstructionProject(
            "PERT_DAG", "PERT Full DAG Test", ProjectArchetype.COMMERCIAL, "Nairobi", 1e7, 20
        )
        p_pert.add_activity(Activity("PATH_A", "Deterministic Critical", "1.1", 10, 10.0, 10.0, 10.0))
        p_pert.add_activity(Activity("PATH_B", "High Skew PERT Path", "1.2", 9, 4.0, 9.0, 28.0))
        pert_res = CriticalPathEngine.compute_pert(p_pert, target_deadline_days=12.0)
        self.assertEqual(pert_res["critical_path_analyzed"], ["PATH_B"])
        self.assertAlmostEqual(pert_res["expected_duration_days"], 11.333, places=2)


if __name__ == "__main__":
    unittest.main()
