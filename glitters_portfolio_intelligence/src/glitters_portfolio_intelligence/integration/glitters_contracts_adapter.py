from __future__ import annotations

from datetime import datetime, timezone
from typing import Sequence
from uuid import UUID

from glitters_contracts import (
    AuthorizedCallerContext,
    PortfolioInvestmentBridge,
)

from ..domain.entities import Portfolio, PropertyAsset
from ..governance.committee_gate import InvestmentDecisionGovernanceGate
from ..optimization.solver_backends import SolverExecutionResult


class GlittersContractsAdapter:
    """Seamless interoperability adapter with glitters_contracts v1.1.0."""

    @classmethod
    def emit_portfolio_bridge(
        cls,
        portfolio: Portfolio,
        solver_result: SolverExecutionResult,
    ) -> PortfolioInvestmentBridge:
        """Constructs the canonical PortfolioInvestmentBridge DTO."""
        holdings = []
        for i, a in enumerate(portfolio.assets):
            if i < len(solver_result.optimal_weights):
                w = float(solver_result.optimal_weights[i])
                holdings.append((a.asset_id, round(w, 4)))

        total_cents = portfolio.total_portfolio_value_cents
        rebal = any(abs(w - (1.0 / max(1, len(portfolio.assets)))) > 0.05 for _, w in holdings)

        return PortfolioInvestmentBridge(
            portfolio_id=portfolio.portfolio_id,
            tenant_org_id=portfolio.tenant_org_id,
            recommended_holdings=tuple(holdings),
            expected_annual_return=solver_result.expected_return,
            portfolio_cvar_95=solver_result.cvar_alpha,
            portfolio_volatility=abs(solver_result.cvar_alpha * 0.75),
            total_capital_deployed_cents=total_cents,
            rebalancing_required=rebal,
            requires_investment_committee_approval=True,
            generated_at_utc=datetime.now(timezone.utc),
        )
