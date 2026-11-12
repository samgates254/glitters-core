"""
15 Mandatory Adversarial Scenarios Test Suite.
"""
import datetime
import time
import unittest

from glitters_construction_engine import (
    ActivityProgressRecord,
    BOQEngine,
    BOQWorkItem,
    CostOptimizationEngine,
    CriticalPathEngine,
    CyclicDependencyError,
    DependencyType,
    EarnedValueAndAnomalyEngine,
    EngineeringSpecification,
    EquipmentOptimizationEngine,
    EquipmentSpec,
    ExecutionMode,
    InfeasibleScheduleError,
    LabourOptimizationEngine,
    LabourRoleSpec,
    MeasurementStatus,
    PrecedenceDependency,
    ProcurementEngine,
    ProcurementRequisition,
    ProfessionalEngineeringApproval,
    ProgressDataOrigin,
    ProjectArchetype,
    ResourceAllocationEngine,
    SchedulingEngine,
    ValueEngineeringAlternative,
    build_kenyan_benchmark_project,
    generate_large_scale_synthetic_project,
    get_verified_kenyan_supplier_quotations,
)


class TestAdversarialScenarios(unittest.TestCase):

    def setUp(self) -> None:
        self.project = build_kenyan_benchmark_project()

    def test_adv_01_impossible_project_schedule(self) -> None:
        self.project.contractual_deadline_days = 30
        with self.assertRaises(InfeasibleScheduleError):
            SchedulingEngine.solve_rcpsp_cpsat(self.project, enforce_contractual_deadline=True)

    def test_adv_02_contradictory_task_dependencies(self) -> None:
        self.project.add_dependency(PrecedenceDependency("A05", "A02", DependencyType.FS, 0))
        with self.assertRaises(CyclicDependencyError):
            CriticalPathEngine.compute_cpm(self.project)

    def test_adv_03_missing_materials(self) -> None:
        quotes = get_verified_kenyan_supplier_quotations()
        with self.assertRaises(InfeasibleScheduleError):
            ProcurementEngine.optimize_supplier_selection_milp(
                material_id="cement_50kg_bags",
                total_demand_quantity=50_000.0,
                required_within_days=10,
                quotations=quotes,
            )

    def test_adv_04_equipment_breakdown(self) -> None:
        fleet = [
            EquipmentSpec("EX_01", "Cat 320D", "Excavator", 250.0, 42000.0, 3500.0, 80000.0, is_operational=False),
            EquipmentSpec("EX_02", "Komatsu PC200", "Excavator", 240.0, 40000.0, 3400.0, 80000.0, is_operational=False),
        ]
        with self.assertRaises(InfeasibleScheduleError):
            EquipmentOptimizationEngine.allocate_equipment_with_breakdown_fallback(
                required_output_units=2000.0, available_days=10, fleet=fleet
            )

    def test_adv_05_labour_shortages(self) -> None:
        skilled = LabourRoleSpec("MASON", "Mason", 1800.0, 350.0, max_available_headcount=2, base_productivity_units_per_day=8.0)
        general = LabourRoleSpec("LAB", "Labourer", 950.0, 180.0, max_available_headcount=4, base_productivity_units_per_day=2.0)
        sup = LabourRoleSpec("SUP", "Supervisor", 4500.0, 750.0, max_available_headcount=1, base_productivity_units_per_day=0.0)
        with self.assertRaises(InfeasibleScheduleError):
            LabourOptimizationEngine.optimize_crew_allocation(
                required_work_units=5000.0, target_duration_days=10,
                skilled_role=skilled, general_role=general, supervisor_role=sup
            )

    def test_adv_06_supplier_failure(self) -> None:
        quotes = get_verified_kenyan_supplier_quotations()
        with self.assertRaises(InfeasibleScheduleError):
            ProcurementEngine.optimize_supplier_selection_milp(
                material_id="cement_50kg_bags",
                total_demand_quantity=500.0,
                required_within_days=2,
                quotations=quotes,
                min_acceptable_reliability=0.995,
            )

    def test_adv_07_sudden_material_price_increases_and_spec_enforcement(self) -> None:
        approval = ProfessionalEngineeringApproval.create_verified_approval(
            approval_id="APP-STR-07",
            engineer_full_name="Eng. Kiprop",
            regulatory_body="EBK",
            registration_number="EBK-A1024",
            license_valid_until=datetime.date(2027, 12, 31),
            approved_drawing_ref="DRW-001",
        )
        spec = EngineeringSpecification(
            spec_id="STR_COL_C25",
            structural_code="BS EN 1992-1-1",
            min_concrete_class_mpa=25.0,
            min_steel_yield_mpa=500.0,
            professional_approval=approval,
        )
        cheap_illegal = ValueEngineeringAlternative(
            "OPT_ILLEGAL_C15", "PKG_RC", "Substandard C15 Concrete (Cheaper)",
            material_cost_kes=4_000_000.0, labour_cost_kes=1_000_000.0, equipment_cost_kes=500_000.0,
            transport_and_procurement_kes=200_000.0, expected_waste_pct=0.05, expected_rework_prob=0.20,
            rework_penalty_kes=1_000_000.0, concrete_mpa_provided=15.0, steel_mpa_provided=500.0
        )
        unknown_strength_illegal = ValueEngineeringAlternative(
            "OPT_UNKNOWN_MPA", "PKG_RC", "Concrete with Missing Strength Evidence (None)",
            material_cost_kes=3_500_000.0, labour_cost_kes=1_000_000.0, equipment_cost_kes=500_000.0,
            transport_and_procurement_kes=200_000.0, expected_waste_pct=0.05, expected_rework_prob=0.10,
            rework_penalty_kes=500_000.0, concrete_mpa_provided=None, steel_mpa_provided=None
        )
        compliant_spiked = ValueEngineeringAlternative(
            "OPT_COMPLIANT_C25", "PKG_RC", "Approved C25/30 Concrete after 25% Cement Price Spike",
            material_cost_kes=7_500_000.0, labour_cost_kes=1_200_000.0, equipment_cost_kes=600_000.0,
            transport_and_procurement_kes=300_000.0, expected_waste_pct=0.05, expected_rework_prob=0.02,
            rework_penalty_kes=500_000.0, concrete_mpa_provided=25.0, steel_mpa_provided=500.0
        )
        res = CostOptimizationEngine.optimize_value_engineering_milp(
            work_package_specs={"PKG_RC": spec},
            alternatives_by_package={"PKG_RC": [cheap_illegal, unknown_strength_illegal, compliant_spiked]},
            budget_ceiling_kes=15_000_000.0,
        )
        self.assertEqual(res["selected_options"][0]["selected_option_id"], "OPT_COMPLIANT_C25")
        self.assertEqual(len(res["rejected_non_compliant_options"]), 2)

    def test_adv_08_extremely_constrained_budget(self) -> None:
        self.project.budget_ceiling_kes = 10_000_000.0
        with self.assertRaises(InfeasibleScheduleError):
            SchedulingEngine.solve_rcpsp_cpsat(self.project, enforce_budget_ceiling=True)

    def test_adv_09_multiple_competing_projects(self) -> None:
        p1 = build_kenyan_benchmark_project("PROJ_WESTLANDS_A")
        p2 = build_kenyan_benchmark_project("PROJ_KILIMANI_B")
        shared_pool = dict(p1.resources)
        res = ResourceAllocationEngine.solve_multi_project_allocation([p1, p2], shared_pool)
        p1_a04_s, p1_a04_f = res["projects"]["PROJ_WESTLANDS_A"]["activity_starts"]["A04"], res["projects"]["PROJ_WESTLANDS_A"]["activity_finishes"]["A04"]
        p2_a04_s, p2_a04_f = res["projects"]["PROJ_KILIMANI_B"]["activity_starts"]["A04"], res["projects"]["PROJ_KILIMANI_B"]["activity_finishes"]["A04"]
        self.assertTrue((p1_a04_f <= p2_a04_s) or (p2_a04_f <= p1_a04_s))

    def test_adv_10_critical_path_changes(self) -> None:
        cpm_before = CriticalPathEngine.compute_cpm(self.project)
        self.assertNotIn("A06", cpm_before["critical_activities"])
        self.project.activities["A06"].duration_days = 50
        cpm_after = CriticalPathEngine.compute_cpm(self.project)
        self.assertIn("A06", cpm_after["critical_activities"])
        self.assertGreater(cpm_after["project_completion_days"], cpm_before["project_completion_days"])

    def test_adv_11_unreliable_productivity_assumptions(self) -> None:
        rec_bad_prod = ActivityProgressRecord(
            activity_id="A06",
            planned_quantity=1000.0,
            actual_quantity=400.0,
            budget_at_completion_kes=6_400_000.0,
            planned_pct_complete_at_status_date=0.50,
            actual_pct_complete_at_status_date=0.35,
            actual_cost_incurred_kes=3_100_000.0,
            planned_material_units=1000.0,
            actual_material_units=520.0,
            planned_labour_hours=2000.0,
            actual_labour_hours=1400.0,
            rework_incidents_count=2,
            unit_procurement_price_paid_kes=920.0,
            benchmark_unit_procurement_price_kes=790.0,
            data_origin=ProgressDataOrigin.ACTUAL_SITE_VERIFIED,
        )
        res = EarnedValueAndAnomalyEngine.evaluate_project_progress([rec_bad_prod])
        anomaly_types = {a["anomaly_type"] for a in res["anomalies"]}
        self.assertIn("LABOUR_PRODUCTIVITY_DETERIORATION", anomaly_types)
        self.assertIn("UNUSUAL_MATERIAL_CONSUMPTION", anomaly_types)
        self.assertIn("PROCUREMENT_IRREGULARITY", anomaly_types)
        self.assertIn("REPEATED_REWORK", anomaly_types)

    def test_adv_12_incomplete_boq(self) -> None:
        boq = BOQEngine("PROJ_INCOMPLETE", ProjectArchetype.RESIDENTIAL)
        boq.add_item(
            BOQWorkItem(
                "ITEM_01", "Excavation", "Topsoil strip", "m3", 150.0,
                MeasurementStatus.VERIFIED_MEASUREMENT, {"diesel_l": 0.5}
            )
        )
        is_complete, issues = boq.validate_completeness({"diesel_l": 180.0})
        self.assertFalse(is_complete)
        self.assertGreaterEqual(len(issues), 10)

    def test_adv_13_duplicate_procurement_requests(self) -> None:
        reqs = [
            ProcurementRequisition("REQ_001", "P1", "A03", "cement_50kg_bags", 1400.0, 15),
            ProcurementRequisition("REQ_001", "P1", "A03", "cement_50kg_bags", 1400.0, 15),
            ProcurementRequisition("REQ_002", "P1", "A03", "cement_50kg_bags", 1400.0, 15),
        ]
        clean, dup_warnings = ProcurementEngine.deduplicate_requisitions(reqs)
        self.assertEqual(len(clean), 1)
        self.assertEqual(len(dup_warnings), 2)

    def test_adv_14_resource_allocation_conflicts(self) -> None:
        self.project.activities["A04"].modes = [
            ExecutionMode("OVER_CRANE", 20, 11_500_000.0, renewable_demands={"TOWER_CRANE": 3.0}, concrete_mpa_offered=25.0, steel_mpa_offered=500.0)
        ]
        with self.assertRaises(InfeasibleScheduleError):
            SchedulingEngine.solve_rcpsp_cpsat(self.project)

    def test_adv_15_large_scale_project_thousands_of_activities(self) -> None:
        mega_proj = generate_large_scale_synthetic_project(num_activities=1200)
        t0 = time.perf_counter()
        cpm_res = CriticalPathEngine.compute_cpm(mega_proj)
        ssgs_res = SchedulingEngine.solve_rcpsp_serial_sgs(mega_proj, priority_rule="LFT")
        elapsed_s = time.perf_counter() - t0
        self.assertEqual(len(ssgs_res["schedule"]), 1200)
        self.assertGreaterEqual(ssgs_res["rcpsp_completion_days"], cpm_res["project_completion_days"])
        self.assertTrue(ssgs_res["schedule_verified"])
        self.assertLess(elapsed_s, 5.0)


if __name__ == "__main__":
    unittest.main()
