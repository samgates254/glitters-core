# Architecture & Module Guide: GLITTERS Engine 08

## 1. Subsystem Architecture

```
glitters_portfolio_intelligence/
├── domain/               # Pure domain entities, exact Money algebra, enums
│   ├── enums.py
│   ├── money.py
│   └── entities.py
├── cashflows/            # Exact cash flow accounting statements (EGI, NOI, CFADS)
│   └── accounting.py
├── returns/              # Investment return metrics (Cap Rate, CoC, NPV, IRR, ROIC)
│   └── metrics.py
├── scenarios/            # 9 canonical stress scenarios, provenance, 1-D CVaR verifier
│   ├── provenance.py
│   ├── stress_engine.py
│   └── cvar_verifier.py
├── optimization/         # Rockafellar-Uryasev LP, constraints, frontier, multi-period
│   ├── constraints.py
│   ├── solver_backends.py
│   ├── rockafellar_cvar.py
│   ├── frontier.py
│   └── multi_period.py
├── governance/           # Advisory envelopes, HumanApprovalRequirement, audit trails
│   └── committee_gate.py
└── integration/          # Typed upstream adapters, glitters_contracts v1.1.0 bridge
    ├── upstream_adapters.py
    └── glitters_contracts_adapter.py
```

## 2. Multi-Solver Integration Pipeline

```
[Portfolio Assets + Scenarios]
            │
            ▼
[RockafellarMeanCVaROptimizer]
            │
    (LP / MILP Model)
            │
            ├───────────────┬───────────────┬───────────────┬───────────────┐
            ▼               ▼               ▼               ▼               ▼
      [HIGHS_SCIPY]   [HIGHS_ORTOOLS] [SCIP_ORTOOLS]  [CBC_ORTOOLS]   [GLOP_ORTOOLS]
            │               │               │               │               │
            └───────────────┴───────────────┴───────────────┴───────────────┘
                                    │
                                    ▼
                        [SolverExecutionResult]
                                    │
                                    ├──────────────────────────┐
                                    ▼                          ▼
                        [cvar_verifier.py]        [committee_gate.py]
                        (Independent 1-D           (Envelopes, Fiduciary
                         CVaR Validation)           Human Approval Gate)
```
