from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional, Sequence
from uuid import UUID
import numpy as np

from glitters_contracts import (
    AuthorizedCallerContext,
    PortfolioInvestmentBridge,
)

from ..domain.entities import Portfolio, PropertyAsset
from ..optimization.solver_backends import SolverExecutionResult


class GlittersContractsAdapter:
    """Seamless interoperability adapter with glitters_contracts v1.1.0."""

    @classmethod
    def emit_portfolio_bridge(
        cls,
        portfolio: Portfolio,
        solver_result: SolverExecutionResult,
        assets: Optional[Sequence[PropertyAsset]] = None,
        scenario_returns: Optional[np.ndarray] = None,
        scenario_probabilities: Optional[np.ndarray] = None,
    ) -> PortfolioInvestmentBridge:
        """Constructs the canonical PortfolioInvestmentBridge DTO with exact holdings and volatility."""
        target_assets = assets if assets is not None else portfolio.assets
        holdings = []
        for i, a in enumerate(target_assets):
            if i < len(solver_result.optimal_weights):
                w = float(solver_result.optimal_weights[i])
                holdings.append((a.asset_id, round(w, 4)))

        total_cents = portfolio.total_portfolio_value_cents
        rebal = any(abs(w - (1.0 / max(1, len(target_assets)))) > 0.05 for _, w in holdings)

        # Compute empirical scenario volatility if provided
        if (
            scenario_returns is not None
            and scenario_probabilities is not None
            and len(solver_result.optimal_weights) == scenario_returns.shape[0]
            and len(scenario_probabilities) > 0
        ):
            port_scen_returns = solver_result.optimal_weights @ scenario_returns
            mu = float(np.sum(scenario_probabilities * port_scen_returns))
            var = float(np.sum(scenario_probabilities * ((port_scen_returns - mu) ** 2)))
            volatility = float(np.sqrt(max(0.0, var)))
        else:
            volatility = abs(solver_result.cvar_alpha)

        return PortfolioInvestmentBridge(
            portfolio_id=portfolio.portfolio_id,
            tenant_org_id=portfolio.tenant_org_id,
            recommended_holdings=tuple(holdings),
            expected_annual_return=solver_result.expected_return,
            portfolio_cvar_95=solver_result.cvar_alpha,
            portfolio_volatility=volatility,
            total_capital_deployed_cents=total_cents,
            rebalancing_required=rebal,
            requires_investment_committee_approval=True,
            generated_at_utc=datetime.now(timezone.utc),
        )
