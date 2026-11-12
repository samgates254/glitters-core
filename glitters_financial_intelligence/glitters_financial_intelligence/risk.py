from __future__ import annotations
from .money import Money
from .accounting import CrossOrganizationAccessError
# MODULE: risk.py
# ==============================================================================
"""
Financial Risk & Stress Testing Engine + Property Financial Intelligence Engine.

Strictly separates:
  - `HISTORICAL_FACTS`: Verified accounting balances from posted journal entries.
  - `MODEL_ASSUMPTIONS`: Explicit parameterized stress shocks and distribution parameters.
  - `SIMULATED_SCENARIO_OUTCOMES`: Counterfactual stress projections, VaR_95, CVaR_95, and LCR.
"""
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_EVEN
from enum import Enum
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np



class EpistemicDataKind(str, Enum):
    HISTORICAL_FACT = "HISTORICAL_FACT"
    MODEL_ASSUMPTION = "MODEL_ASSUMPTION"
    SIMULATED_SCENARIO = "SIMULATED_SCENARIO"


@dataclass(frozen=True, slots=True)
class BaselinePortfolioFacts:
    """Verified historical facts for a property portfolio or organization (in base currency minor units)."""
    organization_id: str
    currency_code: str
    gross_potential_rent_minor: int
    current_vacancy_loss_minor: int
    other_property_income_minor: int
    maintenance_expenditure_minor: int
    other_operating_expense_minor: int
    supplier_liabilities_due_30d_minor: int
    owner_disbursements_due_30d_minor: int
    construction_commitments_30d_minor: int
    debt_service_minor: int
    unhedged_fx_liability_minor: int
    available_cash_reserves_minor: int
    largest_single_tenant_rent_minor: int
    total_billed_rent_minor: int
    total_collected_rent_minor: int
    total_arrears_minor: int
    property_valuation_minor: int
    management_commission_rate_bps: int = 1000  # e.g., 10.00% = 1000 bps
    data_kind: EpistemicDataKind = EpistemicDataKind.HISTORICAL_FACT


@dataclass(frozen=True, slots=True)
class StressScenarioAssumptions:
    """Explicitly tagged stress-testing assumptions."""
    scenario_name: str
    vacancy_rate_increase_pct: Decimal = Decimal("0.00")        # e.g., Decimal("0.20") for +20% vacancy shock
    maintenance_increase_pct: Decimal = Decimal("0.00")         # e.g., Decimal("0.15") for +15% maintenance surge
    collection_delay_haircut_pct: Decimal = Decimal("0.00")     # e.g., Decimal("0.25") for delayed rent collection
    major_tenant_default: bool = False                          # 100% default of largest tenant
    supplier_disruption_cost_surge_pct: Decimal = Decimal("0.00")
    fx_depreciation_pct: Decimal = Decimal("0.00")              # e.g., Decimal("0.15") KES depreciation on FX liabilities
    interest_rate_shock_bps: int = 0
    construction_cost_overrun_pct: Decimal = Decimal("0.00")
    data_kind: EpistemicDataKind = EpistemicDataKind.MODEL_ASSUMPTION


@dataclass(frozen=True, slots=True)
class StressTestResult:
    """Simulated scenario outcome clearly distinguished from historical facts."""
    organization_id: str
    scenario_name: str
    historical_facts: BaselinePortfolioFacts
    assumptions: StressScenarioAssumptions
    stressed_effective_gross_income_minor: int
    stressed_operating_expenses_minor: int
    stressed_noi_minor: int
    noi_delta_minor: int
    stressed_net_liquidity_position_minor: int
    liquidity_shortage_minor: int
    liquidity_coverage_ratio: Decimal
    debt_service_coverage_ratio: Decimal
    monte_carlo_var_95_minor: int
    monte_carlo_cvar_95_minor: int
    data_kind: EpistemicDataKind = EpistemicDataKind.SIMULATED_SCENARIO


@dataclass(frozen=True, slots=True)
class PropertyFinancialKPIs:
    """
    Exact Property-Level and Portfolio-Level Financial Intelligence Metrics.
    Monetary values are exact integer minor units (`Money`); ratios are exact `Decimal` (4 d.p.).
    """
    organization_id: str
    property_id: str
    currency_code: str
    gross_rental_income: Money
    effective_gross_income: Money
    operating_expenses: Money
    net_operating_income: Money
    rental_yield_pct: Decimal
    operating_expense_ratio_pct: Decimal
    arrears_ratio_pct: Decimal
    collection_efficiency_pct: Decimal
    maintenance_expenditure_ratio_pct: Decimal
    management_commission: Money
    owner_distribution_liability: Money
    property_level_profitability: Money


def _safe_pct(numerator: int, denominator: int) -> Decimal:
    if denominator == 0:
        return Decimal("0.0000")
    val = (Decimal(numerator) * Decimal(100)) / Decimal(denominator)
    return val.quantize(Decimal("0.0001"), rounding=ROUND_HALF_EVEN)


class RiskAndStressTestingEngine:
    """
    Evaluates standard and custom real-estate stress scenarios and computes
    Monte Carlo Cash-at-Risk (VaR_95) and Conditional Value-at-Risk (CVaR_95 / Expected Shortfall).
    """

    @staticmethod
    def standard_scenarios() -> List[StressScenarioAssumptions]:
        return [
            StressScenarioAssumptions(
                scenario_name="20% Vacancy Increase Shock",
                vacancy_rate_increase_pct=Decimal("0.20"),
            ),
            StressScenarioAssumptions(
                scenario_name="15% Maintenance Expenditure Surge",
                maintenance_increase_pct=Decimal("0.15"),
            ),
            StressScenarioAssumptions(
                scenario_name="Delayed Rent Collection (30% 30-Day Lag)",
                collection_delay_haircut_pct=Decimal("0.30"),
            ),
            StressScenarioAssumptions(
                scenario_name="Major Anchor Tenant Default",
                major_tenant_default=True,
            ),
            StressScenarioAssumptions(
                scenario_name="Supplier Payment Disruption & Emergency Replacement (+20%)",
                supplier_disruption_cost_surge_pct=Decimal("0.20"),
            ),
            StressScenarioAssumptions(
                scenario_name="15% Foreign-Exchange Depreciation on USD/EUR Liabilities",
                fx_depreciation_pct=Decimal("0.15"),
            ),
            StressScenarioAssumptions(
                scenario_name="Combined Severe Stagflation & Construction Overrun Stress",
                vacancy_rate_increase_pct=Decimal("0.20"),
                maintenance_increase_pct=Decimal("0.15"),
                collection_delay_haircut_pct=Decimal("0.20"),
                major_tenant_default=True,
                supplier_disruption_cost_surge_pct=Decimal("0.10"),
                fx_depreciation_pct=Decimal("0.15"),
                construction_cost_overrun_pct=Decimal("0.20"),
            ),
        ]

    def evaluate_scenario(
        self,
        facts: BaselinePortfolioFacts,
        assumptions: StressScenarioAssumptions,
        n_simulations: int = 2000,
        random_state: int = 42,
    ) -> StressTestResult:
        ccy = facts.currency_code
        base_egi = facts.gross_potential_rent_minor - facts.current_vacancy_loss_minor + facts.other_property_income_minor
        base_opex = facts.maintenance_expenditure_minor + facts.other_operating_expense_minor
        base_noi = base_egi - base_opex

        # Apply vacancy shock and major tenant default
        add_vacancy = Money(facts.gross_potential_rent_minor, ccy).multiply_exact(assumptions.vacancy_rate_increase_pct).minor_units
        tenant_default_loss = facts.largest_single_tenant_rent_minor if assumptions.major_tenant_default else 0
        stressed_rent = max(0, facts.gross_potential_rent_minor - facts.current_vacancy_loss_minor - add_vacancy - tenant_default_loss)

        # Apply collection delay haircut
        delay_loss = Money(stressed_rent, ccy).multiply_exact(assumptions.collection_delay_haircut_pct).minor_units
        stressed_egi = stressed_rent - delay_loss + facts.other_property_income_minor

        # Apply maintenance and supplier disruption shocks
        maint_surge = Money(facts.maintenance_expenditure_minor, ccy).multiply_exact(assumptions.maintenance_increase_pct).minor_units
        supp_surge = Money(facts.other_operating_expense_minor, ccy).multiply_exact(assumptions.supplier_disruption_cost_surge_pct).minor_units
        fx_shock = Money(facts.unhedged_fx_liability_minor, ccy).multiply_exact(assumptions.fx_depreciation_pct).minor_units
        constr_overrun = Money(facts.construction_commitments_30d_minor, ccy).multiply_exact(assumptions.construction_cost_overrun_pct).minor_units

        stressed_opex = base_opex + maint_surge + supp_surge
        stressed_noi = stressed_egi - stressed_opex
        noi_delta = stressed_noi - base_noi

        # 30-day liquidity position = Available Cash + Stressed Cash Inflows - Mandatory 30d Outflows
        total_outflows_30d = (
            stressed_opex
            + facts.supplier_liabilities_due_30d_minor
            + facts.owner_disbursements_due_30d_minor
            + facts.construction_commitments_30d_minor
            + constr_overrun
            + facts.debt_service_minor
            + fx_shock
        )
        available_liquidity = facts.available_cash_reserves_minor + stressed_egi
        stressed_net_liq = available_liquidity - total_outflows_30d
        shortage = max(0, -stressed_net_liq)

        lcr = (
            (Decimal(available_liquidity) / Decimal(total_outflows_30d)).quantize(Decimal("0.0001"), rounding=ROUND_HALF_EVEN)
            if total_outflows_30d > 0
            else Decimal("999.9999")
        )
        dscr = (
            (Decimal(stressed_noi) / Decimal(facts.debt_service_minor)).quantize(Decimal("0.0001"), rounding=ROUND_HALF_EVEN)
            if facts.debt_service_minor > 0
            else Decimal("999.9999")
        )

        # Monte Carlo distribution of stressed net liquidity loss
        rng = np.random.default_rng(random_state)
        egi_draws = rng.normal(loc=float(stressed_egi), scale=max(1.0, 0.08 * float(stressed_egi)), size=n_simulations)
        out_draws = rng.normal(loc=float(total_outflows_30d), scale=max(1.0, 0.06 * float(total_outflows_30d)), size=n_simulations)
        losses = np.maximum(0.0, out_draws - (float(facts.available_cash_reserves_minor) + egi_draws))
        var_95 = float(np.percentile(losses, 95))
        tail_losses = losses[losses >= var_95]
        cvar_95 = float(np.mean(tail_losses)) if tail_losses.size > 0 else var_95

        return StressTestResult(
            organization_id=facts.organization_id,
            scenario_name=assumptions.scenario_name,
            historical_facts=facts,
            assumptions=assumptions,
            stressed_effective_gross_income_minor=stressed_egi,
            stressed_operating_expenses_minor=stressed_opex,
            stressed_noi_minor=stressed_noi,
            noi_delta_minor=noi_delta,
            stressed_net_liquidity_position_minor=stressed_net_liq,
            liquidity_shortage_minor=shortage,
            liquidity_coverage_ratio=lcr,
            debt_service_coverage_ratio=dscr,
            monte_carlo_var_95_minor=int(round(var_95)),
            monte_carlo_cvar_95_minor=int(round(cvar_95)),
        )


class PropertyPortfolioAnalyticsEngine:
    """
    Calculates exact property-level and portfolio-aggregated financial intelligence
    while enforcing strict organization and property boundaries.
    """

    @staticmethod
    def compute_property_kpis(
        organization_id: str,
        property_id: str,
        currency_code: str,
        gross_potential_rent_minor: int,
        vacancy_and_credit_loss_minor: int,
        service_charge_and_other_income_minor: int,
        maintenance_expense_minor: int,
        utilities_insurance_other_opex_minor: int,
        total_billed_minor: int,
        total_collected_minor: int,
        total_arrears_minor: int,
        property_acquisition_cost_minor: int,
        management_commission_rate_bps: int = 1000,
        reserve_retention_minor: int = 0,
    ) -> PropertyFinancialKPIs:
        gri = Money(gross_potential_rent_minor, currency_code)
        egi_minor = gross_potential_rent_minor - vacancy_and_credit_loss_minor + service_charge_and_other_income_minor
        egi = Money(egi_minor, currency_code)
        opex_minor = maintenance_expense_minor + utilities_insurance_other_opex_minor
        opex = Money(opex_minor, currency_code)
        noi_minor = egi_minor - opex_minor
        noi = Money(noi_minor, currency_code)

        # Management commission calculated on collected rent using exact rational rate (bps / 10000)
        comm_factor = Decimal(management_commission_rate_bps) / Decimal(10000)
        mgmt_comm = Money(max(0, total_collected_minor), currency_code).multiply_exact(comm_factor)

        # Owner distribution liability = max(0, Collected Rent - Operating Expenses - Mgmt Commission - Reserve Retention)
        owner_dist_minor = max(
            0,
            total_collected_minor - opex_minor - mgmt_comm.minor_units - reserve_retention_minor,
        )
        owner_dist = Money(owner_dist_minor, currency_code)
        prop_profit = Money(noi_minor - mgmt_comm.minor_units, currency_code)

        return PropertyFinancialKPIs(
            organization_id=organization_id,
            property_id=property_id,
            currency_code=currency_code,
            gross_rental_income=gri,
            effective_gross_income=egi,
            operating_expenses=opex,
            net_operating_income=noi,
            rental_yield_pct=_safe_pct(noi_minor, property_acquisition_cost_minor),
            operating_expense_ratio_pct=_safe_pct(opex_minor, egi_minor),
            arrears_ratio_pct=_safe_pct(total_arrears_minor, total_billed_minor),
            collection_efficiency_pct=_safe_pct(total_collected_minor, total_billed_minor),
            maintenance_expenditure_ratio_pct=_safe_pct(maintenance_expense_minor, egi_minor),
            management_commission=mgmt_comm,
            owner_distribution_liability=owner_dist,
            property_level_profitability=prop_profit,
        )

    @staticmethod
    def aggregate_portfolio(
        organization_id: str,
        property_kpis: Sequence[PropertyFinancialKPIs],
        total_portfolio_valuation_minor: int,
        total_billed_minor: int,
        total_collected_minor: int,
        total_arrears_minor: int,
        total_maintenance_minor: int,
    ) -> PropertyFinancialKPIs:
        if not property_kpis:
            raise ValueError("property_kpis cannot be empty")
        ccy = property_kpis[0].currency_code
        for pk in property_kpis:
            if pk.organization_id != organization_id:
                raise CrossOrganizationAccessError(
                    f"Property '{pk.property_id}' belongs to org '{pk.organization_id}', cannot aggregate into '{organization_id}'"
                )
            if pk.currency_code != ccy:
                raise ValueError("All property KPIs must be converted to organization base currency before aggregation")

        gri = sum((pk.gross_rental_income for pk in property_kpis), Money.zero(ccy))
        egi = sum((pk.effective_gross_income for pk in property_kpis), Money.zero(ccy))
        opex = sum((pk.operating_expenses for pk in property_kpis), Money.zero(ccy))
        noi = sum((pk.net_operating_income for pk in property_kpis), Money.zero(ccy))
        comm = sum((pk.management_commission for pk in property_kpis), Money.zero(ccy))
        owner_dist = sum((pk.owner_distribution_liability for pk in property_kpis), Money.zero(ccy))
        profit = sum((pk.property_level_profitability for pk in property_kpis), Money.zero(ccy))

        return PropertyFinancialKPIs(
            organization_id=organization_id,
            property_id="PORTFOLIO_AGGREGATE",
            currency_code=ccy,
            gross_rental_income=gri,
            effective_gross_income=egi,
            operating_expenses=opex,
            net_operating_income=noi,
            rental_yield_pct=_safe_pct(noi.minor_units, total_portfolio_valuation_minor),
            operating_expense_ratio_pct=_safe_pct(opex.minor_units, egi.minor_units),
            arrears_ratio_pct=_safe_pct(total_arrears_minor, total_billed_minor),
            collection_efficiency_pct=_safe_pct(total_collected_minor, total_billed_minor),
            maintenance_expenditure_ratio_pct=_safe_pct(total_maintenance_minor, egi.minor_units),
            management_commission=comm,
            owner_distribution_liability=owner_dist,
            property_level_profitability=profit,
        )

# ==============================================================================
