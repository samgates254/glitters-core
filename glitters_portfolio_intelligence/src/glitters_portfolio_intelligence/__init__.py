from __future__ import annotations

"""
GLITTERS Engine 08: Portfolio Optimization & Investment Intelligence
Package: glitters_portfolio_intelligence
Version: 0.8.0
Proprietary - GLITTERS Algorithmic Platform
"""

from .domain.enums import (
    AssetCategory,
    GeographicSubmarket,
    DecisionAction,
    OwnershipRepresentation,
    OptimizationObjective,
    SolverBackendType,
    OptimizationStatus,
    ScenarioProvenanceKind,
    IRRStatus,
)
from .domain.money import Money, allocate_cents
from .domain.entities import PropertyAsset, Portfolio

from .cashflows.accounting import (
    AnnualCashFlowStatement,
    compute_asset_annual_cashflow,
    compute_portfolio_annual_cashflow,
)

from .returns.metrics import (
    IRRResult,
    compute_cap_rate,
    compute_cash_on_cash,
    compute_npv,
    compute_irr,
    compute_equity_multiple,
    compute_roic,
    compute_multi_period_total_return,
)

from .scenarios.provenance import ScenarioDefinition
from .scenarios.cvar_verifier import CVaRVerificationResult, compute_rockafellar_cvar_1d
from .scenarios.stress_engine import ScenarioStressEngine

from .optimization.constraints import PortfolioConstraints, build_linear_constraint_matrices
from .optimization.solver_backends import (
    PortfolioSolverBackend,
    SolverExecutionResult,
)
from .optimization.rockafellar_cvar import RockafellarMeanCVaROptimizer
from .optimization.frontier import (
    FrontierPoint,
    EfficientFrontierResult,
    EfficientFrontierExplorer,
)
from .optimization.multi_period import (
    MultiPeriodDecisionStep,
    MultiPeriodOptimizationResult,
    MultiPeriodPortfolioOptimizer,
)

from .governance.committee_gate import InvestmentDecisionGovernanceGate
from .integration.upstream_adapters import UpstreamEngineDataFeed, UpstreamIntegrationAdapter
from .integration.glitters_contracts_adapter import GlittersContractsAdapter

__version__ = "0.8.0"

__all__ = [
    # Domain
    "AssetCategory",
    "GeographicSubmarket",
    "DecisionAction",
    "OwnershipRepresentation",
    "OptimizationObjective",
    "SolverBackendType",
    "OptimizationStatus",
    "ScenarioProvenanceKind",
    "IRRStatus",
    "Money",
    "allocate_cents",
    "PropertyAsset",
    "Portfolio",
    # Cashflows
    "AnnualCashFlowStatement",
    "compute_asset_annual_cashflow",
    "compute_portfolio_annual_cashflow",
    # Returns
    "IRRResult",
    "compute_cap_rate",
    "compute_cash_on_cash",
    "compute_npv",
    "compute_irr",
    "compute_equity_multiple",
    "compute_roic",
    "compute_multi_period_total_return",
    # Scenarios
    "ScenarioDefinition",
    "CVaRVerificationResult",
    "compute_rockafellar_cvar_1d",
    "ScenarioStressEngine",
    # Optimization
    "PortfolioConstraints",
    "build_linear_constraint_matrices",
    "PortfolioSolverBackend",
    "SolverExecutionResult",
    "RockafellarMeanCVaROptimizer",
    "FrontierPoint",
    "EfficientFrontierResult",
    "EfficientFrontierExplorer",
    "MultiPeriodDecisionStep",
    "MultiPeriodOptimizationResult",
    "MultiPeriodPortfolioOptimizer",
    # Governance & Integration
    "InvestmentDecisionGovernanceGate",
    "UpstreamEngineDataFeed",
    "UpstreamIntegrationAdapter",
    "GlittersContractsAdapter",
]
