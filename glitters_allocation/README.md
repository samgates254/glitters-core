# `glitters_allocation`: GLITTERS Property Allocation & Optimization Engine

A mathematically rigorous, deterministic, explainable, constraint-driven property allocation and optimization engine designed for **GLITTERS**, a multi-tenant real estate and property operations ecosystem operating initially in Kenya.

## Key Capabilities & Invariants

1. **Zero-Django-ORM Isolation**: Pure functional/service domain package (`glitters_allocation`) with frozen, validated contract dataclasses and zero coupling to Django ORM internals.
2. **Exact Integer Financial & Scoring Arithmetic**: All monetary quantities are denominated in integer Kenyan Cents (`1 KES = 100 cents`) and all policy weights in integer basis points (`10,000 bps = 100%`), eliminating IEEE-754 floating-point drift across CPU architectures.
3. **Six Mathematical Optimization Regimes**:
   - `MIN_COST_MAX_FLOW`: OR-Tools Cost-Scaling Push-Relabel / Network Simplex over Totally Unimodular (TU) capacitated bipartite flow networks.
   - `HUNGARIAN_ASSIGNMENT`: Modified Jonker-Volgenant $O(V^3)$ bipartite matching via SciPy.
   - `MILP_HIGHS`: Sparse branch-and-cut Mixed-Integer Linear Programming via HiGHS.
   - `CPSAT_EXACT`: Constraint Programming with SAT for side-constrained portfolios (co-occupancy conflict groups, organizational floor/ceiling quotas, demographic proportionality parity, and churn caps).
   - `LEXICOGRAPHIC_CPSAT`: Multi-stage exact lexicographic optimization (Stage 1: Maximize total allocation coverage; Stage 2: Maximize composite utility + cryptographic tie-break score).
   - `LAGRANGIAN_DECOMPOSITION`: Bounded-approximation solver with certified dual upper bounds for massive side-constrained instances.
4. **Cryptographic Tie-Breaking**: SHA-256 hash ranking over `(policy_id, policy_version, applicant_id, unit_id)` scaled strictly below the minimum composite score unit (`1 / E_scale`), guaranteeing a strict total ordering of equal-scoring candidates without ever overriding a higher optimization score.
5. **Independent Post-Solve Verification Barrier**: Every allocation batch undergoes an independent $O(N)$ verification pass (`verify_post_solve_invariants`) checking all 10 Critical Invariants before returning.

## Running the Test & Benchmark Suites

```bash
python3 tests/test_allocation_engine.py
python3 benchmarks/run_benchmarks.py
```
