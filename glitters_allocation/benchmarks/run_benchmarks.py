"""
Live Empirical Performance & Scalability Benchmark Suite for GLITTERS Allocation Engine.
Executes real solver runs across varying portfolio sizes and solver regimes, measuring
wall-clock execution time, graph build time, feasible edge count, and memory footprint.
"""

from __future__ import annotations

import json
import random
import sys
import time
import tracemalloc
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from glitters_allocation import (
    AllocationBatchInput,
    AllocationPolicy,
    ApplicantProfile,
    GlittersAllocationEngine,
    InventorySnapshot,
    PropertyCategory,
    PropertyUnit,
    SolverRegime,
)


def generate_synthetic_portfolio(
    n_units: int,
    n_applicants: int,
    max_dist_km: float = 4.0,
    with_conflicts: bool = False,
    seed: int = 42,
) -> InventorySnapshot:
    rng = random.Random(seed)
    zones = ["NBO-WESTLANDS", "NBO-KILIMANI", "NBO-UPPERHILL", "NBO-KAREN", "NBO-LAVINGTON"]
    categories = [
        PropertyCategory.RESIDENTIAL_AFFORDABLE,
        PropertyCategory.RESIDENTIAL_MARKET,
        PropertyCategory.RESIDENTIAL_EXECUTIVE,
    ]

    units = []
    for k in range(n_units):
        cat = categories[k % len(categories)]
        base_rent = 2_500_000 if cat == PropertyCategory.RESIDENTIAL_AFFORDABLE else 6_000_000
        units.append(
            PropertyUnit(
                unit_id=f"U-{k:05d}",
                property_id=f"P-{k // 4:05d}",
                organization_id="ORG-GLITTERS-KE",
                category=cat,
                zone_code=zones[k % len(zones)],
                latitude=-1.2500 - (k % 100) * 0.0015,
                longitude=36.7800 + (k // 100) * 0.0015,
                monthly_rent_cents=base_rent + (k % 8) * 300_000,
                security_deposit_cents=base_rent,
                service_charge_cents=300_000,
                min_credit_score=550 + (k % 5) * 20,
                min_income_multiple_bps=25_000,
                capacity_slots=1,
                bedrooms=1 + (k % 3),
                floor_area_sqm=45 + (k % 4) * 20,
                available_from_day=0,
                available_until_day=1095,
                min_lease_days=180,
                max_lease_days=730,
                amenities=frozenset({"PARKING", "SECURITY"}),
                owner_priority_tier=1 + (k % 5),
            )
        )

    applicants = []
    for i in range(n_applicants):
        cat = categories[i % len(categories)]
        applicants.append(
            ApplicantProfile(
                applicant_id=f"A-{i:05d}",
                organization_id="ORG-GLITTERS-KE",
                allowed_categories=frozenset({cat}),
                max_monthly_budget_cents=9_500_000,
                max_upfront_capital_cents=25_000_000,
                verified_monthly_income_cents=30_000_000,
                credit_score=680 + (i % 100),
                preferred_latitude=-1.2500 - (i % 100) * 0.0015,
                preferred_longitude=36.7800 + (i // 100) * 0.0015,
                max_travel_distance_km=max_dist_km,
                min_bedrooms=1,
                min_floor_area_sqm=40,
                desired_move_in_day=15,
                latest_move_in_day=90,
                desired_lease_days=365,
                preferred_amenities=frozenset({"PARKING"}),
                preferred_zones=frozenset({zones[rng.randint(0, len(zones) - 1)]}),
                conflict_group_id=f"CG-{i % 25}" if (with_conflicts and i % 5 == 0) else None,
                urgency_level=1 + (i % 10),
                vulnerability_priority=i % 6,
                demographic_group="GROUP_A" if i % 2 == 0 else "GROUP_B",
            )
        )

    return InventorySnapshot(
        snapshot_id=f"BENCH-{n_units}x{n_applicants}",
        snapshot_sequence=1,
        timestamp_iso="2026-11-12T15:00:00Z",
        units=tuple(units),
        applicants=tuple(applicants),
    )


def run_benchmarks() -> list[dict]:
    engine = GlittersAllocationEngine()
    scenarios = [
        ("Small Portfolio (50 U x 100 A) - Hungarian", 50, 100, 10.0, False, SolverRegime.HUNGARIAN_ASSIGNMENT),
        ("Small Portfolio (50 U x 100 A) - MinCostMaxFlow", 50, 100, 10.0, False, SolverRegime.MIN_COST_MAX_FLOW),
        ("Small Portfolio (50 U x 100 A) - MILP HiGHS", 50, 100, 10.0, False, SolverRegime.MILP_HIGHS),
        ("Small Portfolio (50 U x 100 A) - CP-SAT Exact", 50, 100, 10.0, False, SolverRegime.CPSAT_EXACT),
        ("Small Portfolio (50 U x 100 A) - Lexicographic CP-SAT", 50, 100, 10.0, True, SolverRegime.LEXICOGRAPHIC_CPSAT),
        ("Medium Portfolio (250 U x 500 A) - MinCostMaxFlow", 250, 500, 5.0, False, SolverRegime.MIN_COST_MAX_FLOW),
        ("Medium Portfolio (250 U x 500 A) - CP-SAT Side-Constrained", 250, 500, 3.5, True, SolverRegime.CPSAT_EXACT),
        ("Large Portfolio (1,000 U x 1,000 A) - MinCostMaxFlow", 1000, 1000, 3.0, False, SolverRegime.MIN_COST_MAX_FLOW),
        ("High Contention (1 U x 2,000 A) - MinCostMaxFlow", 1, 2000, 25.0, False, SolverRegime.MIN_COST_MAX_FLOW),
        ("Enterprise Scale (2,000 U x 2,000 A) - MinCostMaxFlow", 2000, 2000, 2.0, False, SolverRegime.MIN_COST_MAX_FLOW),
    ]

    results = []
    print(f"{'Scenario':<55} | {'Alloc':>5} | {'Time (ms)':>10} | {'Peak KB':>9} | {'Status'}")
    print("-" * 100)

    for label, n_u, n_a, dist_km, conflicts, regime in scenarios:
        snap = generate_synthetic_portfolio(n_u, n_a, max_dist_km=dist_km, with_conflicts=conflicts)
        pol = AllocationPolicy(
            policy_id=f"POL-{regime.value}",
            policy_version="v1.0",
            organization_id="ORG-GLITTERS-KE",
            solver_regime=regime,
            enforce_demographic_proportionality=conflicts,
        )
        batch_in = AllocationBatchInput(
            request_id=f"BENCH-{label}",
            expected_snapshot_sequence=1,
            expected_policy_version="v1.0",
            snapshot=snap,
            policy=pol,
        )

        tracemalloc.start()
        t0 = time.perf_counter()
        res = engine.allocate(batch_in)
        wall_ms = (time.perf_counter() - t0) * 1000.0
        _, peak_bytes = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        peak_kb = peak_bytes / 1024.0
        row = {
            "scenario": label,
            "units": n_u,
            "applicants": n_a,
            "regime": res.solver_regime_used.value,
            "allocated_count": len(res.decisions),
            "execution_time_ms": round(wall_ms, 2),
            "peak_memory_kb": round(peak_kb, 1),
            "status": res.status.value,
            "is_proven_optimal": res.is_proven_optimal,
            "jain_fairness_bps": res.fairness_metrics.jain_fairness_index_bps,
        }
        results.append(row)
        print(f"{label:<55} | {len(res.decisions):>5} | {wall_ms:>10.2f} | {peak_kb:>9.1f} | {res.status.value}")

    out_path = Path(__file__).resolve().parent / "benchmark_results.json"
    out_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
    return results


if __name__ == "__main__":
    run_benchmarks()
