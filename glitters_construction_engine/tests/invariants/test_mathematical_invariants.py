"""
Property-Based Mathematical Invariant Tests (25 Random Precedence DAGs & 30 EOQ Convexity Curves).
"""
import unittest
import numpy as np
from glitters_construction_engine import (
    Activity,
    ConstructionProject,
    CriticalPathEngine,
    DependencyType,
    InventoryOptimizationEngine,
    PrecedenceDependency,
    ProjectArchetype,
    ResourceCategory,
    ResourcePoolItem,
    SchedulingEngine,
)


class TestMathematicalInvariants(unittest.TestCase):

    def test_property_based_dag_cpm_and_rcpsp_invariants(self) -> None:
        rng = np.random.default_rng(2026)
        for trial in range(25):
            n_nodes = int(rng.integers(6, 18))
            p = ConstructionProject(
                project_id=f"PROP_DAG_{trial}",
                name="Property Test DAG",
                archetype=ProjectArchetype.COMMERCIAL,
                county="Nairobi",
                budget_ceiling_kes=1e9,
                contractual_deadline_days=1000,
            )
            p.resources["CREW"] = ResourcePoolItem(
                "CREW", "Crew", ResourceCategory.HUMAN, "Worker", "person", 10.0, 1500.0
            )
            for i in range(n_nodes):
                dur = int(rng.integers(1, 15))
                req = float(rng.integers(2, 7))
                p.add_activity(
                    Activity(f"N{i:02d}", f"Task {i}", "1.1", dur, resource_demands={"CREW": req})
                )
                for j in range(i):
                    if rng.random() < 0.30:
                        p.add_dependency(PrecedenceDependency(f"N{j:02d}", f"N{i:02d}", DependencyType.FS, 0))

            cpm = CriticalPathEngine.compute_cpm(p)
            for aid, m in cpm["metrics"].items():
                self.assertEqual(m.earliest_start + m.duration_days, m.earliest_finish)
                self.assertEqual(m.latest_start + m.duration_days, m.latest_finish)
                self.assertGreaterEqual(m.total_float, m.free_float)
                self.assertGreaterEqual(m.free_float, 0)
            self.assertGreater(len(cpm["critical_activities"]), 0)

            ssgs = SchedulingEngine.solve_rcpsp_serial_sgs(p)
            self.assertGreaterEqual(ssgs["rcpsp_completion_days"], cpm["project_completion_days"])
            self.assertTrue(ssgs["schedule_verified"])

    def test_property_based_eoq_optimality_condition(self) -> None:
        rng = np.random.default_rng(999)
        for _ in range(30):
            D = float(rng.uniform(500.0, 50000.0))
            S = float(rng.uniform(5000.0, 80000.0))
            h_day = float(rng.uniform(0.5, 25.0))
            horizon = int(rng.integers(90, 365))
            res = InventoryOptimizationEngine.compute_eoq_and_reorder_policy(
                annual_or_project_demand=D,
                horizon_days=horizon,
                ordering_cost_per_order_kes=S,
                holding_cost_per_unit_per_day_kes=h_day,
                mean_daily_demand=D / horizon,
                std_daily_demand=2.0,
                mean_lead_time_days=5.0,
                std_lead_time_days=1.0,
            )
            self.assertAlmostEqual(
                res["total_ordering_cost_kes"], res["cycle_holding_cost_kes"], delta=50.0
            )


if __name__ == "__main__":
    unittest.main()
