from __future__ import annotations

from typing import Dict, List, Mapping, Sequence
import numpy as np

from ..domain.entities import Portfolio, PropertyAsset
from ..domain.enums import AssetCategory, GeographicSubmarket, ScenarioProvenanceKind
from .provenance import ScenarioDefinition


class ScenarioStressEngine:
    """
    Reproducible scenario generator with strict epistemic provenance tracking.
    Generates the 9 canonical GLITTERS stress scenarios across macroeconomic,
    monetary, rental, construction, and sub-market risk factors.
    """

    @classmethod
    def get_canonical_scenarios(cls) -> List[ScenarioDefinition]:
        """Returns the 9 standard macroeconomic and real-estate scenario definitions."""
        return [
            ScenarioDefinition(
                scenario_id="SCEN_BASE",
                name="Baseline Macroeconomic Outlook",
                provenance=ScenarioProvenanceKind.STATISTICAL_FORECAST,
                source="Central Bank of Kenya & KNBS Macro Outlook 2026",
                observation_or_forecast_period="2026-2028 (Annualized)",
                probability=0.35,
                model_version="macro_var_v1.0.0",
                transformation_methodology="Vector Autoregression (VAR) baseline conditional mean",
                assumptions={
                    "cpi_inflation": 0.065,
                    "policy_rate": 0.120,
                    "gdp_growth": 0.052,
                    "rental_growth": 0.050,
                    "cap_rate_shift_bps": 0.0,
                    "construction_inflation": 0.070,
                },
            ),
            ScenarioDefinition(
                scenario_id="SCEN_INFLATION",
                name="High Inflation Surge",
                provenance=ScenarioProvenanceKind.EXPERT_ASSUMPTION,
                source="GLITTERS Investment Committee Adverse Stress Parameters",
                observation_or_forecast_period="2026-2027 Stress Window",
                probability=0.15,
                model_version="stress_rules_v1.0.0",
                transformation_methodology="Deterministic cost-push inflation shock with lagged rental adjustment",
                assumptions={
                    "cpi_inflation": 0.115,
                    "policy_rate": 0.145,
                    "opex_surge_pct": 0.180,
                    "rental_growth": 0.030,
                    "cap_rate_shift_bps": 50.0,
                    "construction_inflation": 0.150,
                },
            ),
            ScenarioDefinition(
                scenario_id="SCEN_RATES",
                name="Interest Rate Shock",
                provenance=ScenarioProvenanceKind.EXPERT_ASSUMPTION,
                source="Monetary Policy Committee Tightening Scenario",
                observation_or_forecast_period="2026-2027 Tightening Cycle",
                probability=0.10,
                model_version="monetary_shock_v1.0.0",
                transformation_methodology="Parallel upward yield curve shift with floating loan re-pricing",
                assumptions={
                    "policy_rate_shock_bps": 350.0,
                    "floating_debt_increase_pct": 0.280,
                    "cap_rate_shift_bps": 85.0,
                    "exit_valuation_haircut": 0.080,
                },
            ),
            ScenarioDefinition(
                scenario_id="SCEN_ASSET_FALL",
                name="Property Price Contraction",
                provenance=ScenarioProvenanceKind.HISTORICAL_OBSERVATION,
                source="Hass Property Index Historic Down-cycle Data (2019-2021)",
                observation_or_forecast_period="Historical 24-Month Property Contraction",
                probability=0.10,
                model_version="historical_replay_v1.0.0",
                transformation_methodology="Empirical percentile replay from historical cadastral and asking indices",
                assumptions={
                    "commercial_office_valuation_drop": 0.150,
                    "residential_valuation_drop": 0.100,
                    "retail_valuation_drop": 0.120,
                    "liquidity_discount_surge": 0.060,
                },
            ),
            ScenarioDefinition(
                scenario_id="SCEN_VACANCY",
                name="Severe Vacancy Spike",
                provenance=ScenarioProvenanceKind.HISTORICAL_OBSERVATION,
                source="KNBS & Knight Frank Commercial Real Estate Occupancy Census",
                observation_or_forecast_period="Historical Economic Disruption Shock",
                probability=0.08,
                model_version="occupancy_decay_v1.0.0",
                transformation_methodology="Empirical vacancy surge by asset class and tenant credit stress",
                assumptions={
                    "commercial_office_vacancy_increase": 0.250,
                    "residential_vacancy_increase": 0.120,
                    "credit_default_surge": 0.150,
                    "collection_delay_days": 45.0,
                },
            ),
            ScenarioDefinition(
                scenario_id="SCEN_STAGNATION",
                name="Rental Stagnation",
                provenance=ScenarioProvenanceKind.EXPERT_ASSUMPTION,
                source="GLITTERS Real Estate Economics Board Consensus",
                observation_or_forecast_period="36-Month Horizon",
                probability=0.08,
                model_version="stagnation_model_v1.0.0",
                transformation_methodology="Zero nominal lease growth under cumulative 7% annual OPEX drift",
                assumptions={
                    "rental_growth": 0.000,
                    "opex_drift_annual": 0.070,
                    "concession_months_per_lease": 1.5,
                },
            ),
            ScenarioDefinition(
                scenario_id="SCEN_CONSTRUCTION",
                name="CapEx & Renovation Cost Surge",
                provenance=ScenarioProvenanceKind.HISTORICAL_OBSERVATION,
                source="BORAQS Materials Price Indices & EBK Cost Registers (2022-2023)",
                observation_or_forecast_period="Historical Post-Supply Chain Shock",
                probability=0.06,
                model_version="construction_surge_v1.0.0",
                transformation_methodology="Empirical cement, steel, and mechanical cost escalation replay",
                assumptions={
                    "capex_cost_escalation": 0.250,
                    "renovation_delay_months": 4.0,
                    "replacement_cost_surge": 0.200,
                },
            ),
            ScenarioDefinition(
                scenario_id="SCEN_REGIONAL_DETER",
                name="Regional Submarket Deterioration",
                provenance=ScenarioProvenanceKind.EXPERT_ASSUMPTION,
                source="County Infrastructure & Zoning Impact Assessment",
                observation_or_forecast_period="2026-2028 County Fiscal Review",
                probability=0.05,
                model_version="spatial_shock_v1.0.0",
                transformation_methodology="Localized traffic, taxation, and utility degradation penalty",
                assumptions={
                    "regional_yield_compression": 0.300,
                    "submarket_default_multiplier": 2.50,
                },
            ),
            ScenarioDefinition(
                scenario_id="SCEN_LIQUIDITY_CRUNCH",
                name="Severe Liquidity Crunch",
                provenance=ScenarioProvenanceKind.SYNTHETIC_SCENARIO,
                source="Synthetic Stress Test Protocol (Basel III / Real Estate Equivalent)",
                observation_or_forecast_period="Simulated 90-Day Liquidity Squeeze",
                probability=0.03,
                model_version="synthetic_tail_v1.0.0",
                transformation_methodology="Synthetic non-linear liquidity freeze with emergency asset disposal haircuts",
                assumptions={
                    "refinancing_availability": 0.000,
                    "emergency_disposal_haircut": 0.220,
                    "unhedged_cash_drain_pct": 0.350,
                },
            ),
        ]

    @classmethod
    def evaluate_portfolio_scenario_returns(
        cls,
        assets: Sequence[PropertyAsset],
        scenarios: Sequence[ScenarioDefinition] | None = None,
    ) -> tuple[np.ndarray, np.ndarray, list[ScenarioDefinition]]:
        """
        Computes the asset return matrix across scenarios:
          return_matrix[i, s]: Annual return of asset i under scenario s
          probability_vector[s]: Probability of scenario s

        Returns: (return_matrix, probability_vector, scenarios)
        """
        scen_list = list(scenarios) if scenarios is not None else cls.get_canonical_scenarios()
        n_assets = len(assets)
        n_scens = len(scen_list)

        probs = np.array([s.probability for s in scen_list], dtype=np.float64)
        probs = probs / np.sum(probs)  # Ensure exact sum to 1.0

        return_matrix = np.zeros((n_assets, n_scens), dtype=np.float64)

        for s_idx, scen in enumerate(scen_list):
            for a_idx, asset in enumerate(assets):
                # Baseline cash-flow yield
                gpr = asset.monthly_gross_potential_rent_cents * 12
                opex = asset.monthly_operating_expense_cents * 12
                val = max(1, asset.current_valuation_cents)

                # Asset-class specific adjustments
                vac = asset.annual_baseline_vacancy_rate
                cred = asset.annual_baseline_credit_loss_rate
                cap_shift = scen.assumptions.get("cap_rate_shift_bps", 0.0) / 10000.0

                if scen.scenario_id == "SCEN_VACANCY":
                    if asset.category == AssetCategory.COMMERCIAL_OFFICE:
                        vac += scen.assumptions.get("commercial_office_vacancy_increase", 0.20)
                    else:
                        vac += scen.assumptions.get("residential_vacancy_increase", 0.10)
                    cred += scen.assumptions.get("credit_default_surge", 0.10)

                elif scen.scenario_id == "SCEN_INFLATION":
                    opex = int(opex * (1.0 + scen.assumptions.get("opex_surge_pct", 0.15)))

                elif scen.scenario_id == "SCEN_ASSET_FALL":
                    if asset.category == AssetCategory.COMMERCIAL_OFFICE:
                        val = int(val * (1.0 - scen.assumptions.get("commercial_office_valuation_drop", 0.15)))
                    else:
                        val = int(val * (1.0 - scen.assumptions.get("residential_valuation_drop", 0.10)))

                vac = min(0.95, max(0.0, vac))
                cred = min(0.95, max(0.0, cred))

                billed = gpr * (1.0 - vac)
                egi = billed * (1.0 - cred)
                noi = egi - opex - asset.annual_routine_maintenance_cents
                debt = asset.annual_debt_service_cents
                capex = asset.planned_capex_cents

                if scen.scenario_id == "SCEN_CONSTRUCTION":
                    capex = int(capex * (1.0 + scen.assumptions.get("capex_cost_escalation", 0.25)))

                cfads = noi - debt - capex
                initial_val = max(1, asset.acquisition_valuation_cents)
                income_yield = cfads / initial_val

                # Capital appreciation with cap rate shift
                appreciation = scen.assumptions.get("rental_growth", 0.04) - cap_shift
                if scen.scenario_id == "SCEN_ASSET_FALL":
                    appreciation = -0.10
                elif scen.scenario_id == "SCEN_LIQUIDITY_CRUNCH":
                    appreciation = -0.15

                total_ret = income_yield + appreciation
                return_matrix[a_idx, s_idx] = float(total_ret)

        return return_matrix, probs, scen_list
