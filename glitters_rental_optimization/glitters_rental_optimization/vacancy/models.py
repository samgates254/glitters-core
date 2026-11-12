# ======================================================================================
"""Vacancy Duration, Optimal Stopping & Rent Reduction Justification Engine."""

from __future__ import annotations

from dataclasses import dataclass
from ..demand.models import RentalDemandModel
from ..schemas import UnitSpecification


@dataclass(frozen=True)
class VacancyDecisionAnalysis:
    """Quantifies the trade-off between holding current asking rent vs reducing price."""

    unit_id: str
    current_asking_rent_kes: float
    candidate_reduced_rent_kes: float
    expected_vacancy_months_at_current: float
    expected_vacancy_months_at_candidate: float
    vacancy_months_saved: float
    expected_financial_cost_of_vacancy_kes: float
    marginal_vacancy_saving_kes: float
    locked_in_lease_revenue_forgone_kes: float
    uncertainty_hurdle_kes: float
    net_economic_benefit_of_cut_kes: float
    recommend_rent_reduction: bool
    rationale: str


class VacancyOptimizationEngine:
    """Evaluates whether a rent reduction is mathematically justified by vacancy cost savings."""

    def __init__(
        self,
        demand_model: RentalDemandModel | None = None,
        hurdle_z_score: float = 1.28,
    ) -> None:
        self.demand_model = demand_model or RentalDemandModel()
        self.hurdle_z_score = hurdle_z_score

    def evaluate_rent_reduction(
        self,
        unit: UnitSpecification,
        candidate_new_rent_kes: float,
        achievable_market_rent_kes: float,
        price_elasticity: float,
        elasticity_std_error: float = 0.20,
        calendar_month: int = 11,
    ) -> VacancyDecisionAnalysis:
        """Compares expected benefit of reducing rent against locked-in revenue sacrifice."""
        current_rent = unit.current_asking_rent_kes or achievable_market_rent_kes
        eval_curr = self.demand_model.evaluate_at_price(
            current_rent, unit, achievable_market_rent_kes, price_elasticity, calendar_month
        )
        eval_new = self.demand_model.evaluate_at_price(
            candidate_new_rent_kes,
            unit,
            achievable_market_rent_kes,
            price_elasticity,
            calendar_month,
        )

        v_curr = eval_curr.expected_vacancy_duration_months
        v_new = eval_new.expected_vacancy_duration_months
        months_saved = max(0.0, v_curr - v_new)

        # Total expected financial cost of remaining vacant at current rent
        monthly_opportunity_plus_holding = (
            achievable_market_rent_kes * (1.0 - unit.baseline_arrears_rate)
            + unit.vacancy_holding_cost_kes
        )
        total_vacancy_cost_kes = v_curr * monthly_opportunity_plus_holding

        if candidate_new_rent_kes >= current_rent:
            return VacancyDecisionAnalysis(
                unit_id=unit.unit_id,
                current_asking_rent_kes=current_rent,
                candidate_reduced_rent_kes=candidate_new_rent_kes,
                expected_vacancy_months_at_current=v_curr,
                expected_vacancy_months_at_candidate=v_new,
                vacancy_months_saved=0.0,
                expected_financial_cost_of_vacancy_kes=total_vacancy_cost_kes,
                marginal_vacancy_saving_kes=0.0,
                locked_in_lease_revenue_forgone_kes=0.0,
                uncertainty_hurdle_kes=0.0,
                net_economic_benefit_of_cut_kes=0.0,
                recommend_rent_reduction=False,
                rationale="Candidate rent is not a reduction below current asking rent.",
            )

        # Marginal vacancy loss saved by cutting price
        marginal_saving_kes = months_saved * (
            candidate_new_rent_kes * (1.0 - eval_new.expected_arrears_rate)
            + unit.vacancy_holding_cost_kes
        )

        # Locked-in revenue forgone over occupied months of the lease term
        lease_term = float(max(unit.typical_lease_term_months, 6))
        net_curr_monthly = current_rent * (1.0 - eval_curr.expected_arrears_rate)
        net_new_monthly = candidate_new_rent_kes * (1.0 - eval_new.expected_arrears_rate)
        forgone_revenue_kes = max(0.0, lease_term * (net_curr_monthly - net_new_monthly))

        # Epistemic uncertainty hurdle to prevent premature knee-jerk cuts in noisy markets
        uncertainty_hurdle = (
            self.hurdle_z_score
            * elasticity_std_error
            * abs(current_rent - candidate_new_rent_kes)
            * 1.5
        )

        net_benefit = marginal_saving_kes - forgone_revenue_kes - uncertainty_hurdle
        justified = net_benefit > 0.0

        if justified:
            rationale = (
                f"Rent reduction justified: accelerates leasing by {months_saved:.2f} months, "
                f"saving KES {marginal_saving_kes:,.0f} in vacancy losses vs "
                f"KES {forgone_revenue_kes:,.0f} lease revenue sacrifice."
            )
        else:
            rationale = (
                f"Hold price (cut rejected): vacancy savings (KES {marginal_saving_kes:,.0f}) "
                f"do not outweigh locked-in lease revenue loss (KES {forgone_revenue_kes:,.0f}) "
                f"plus uncertainty hurdle (KES {uncertainty_hurdle:,.0f})."
            )

        return VacancyDecisionAnalysis(
            unit_id=unit.unit_id,
            current_asking_rent_kes=current_rent,
            candidate_reduced_rent_kes=candidate_new_rent_kes,
            expected_vacancy_months_at_current=v_curr,
            expected_vacancy_months_at_candidate=v_new,
            vacancy_months_saved=months_saved,
            expected_financial_cost_of_vacancy_kes=total_vacancy_cost_kes,
            marginal_vacancy_saving_kes=marginal_saving_kes,
            locked_in_lease_revenue_forgone_kes=forgone_revenue_kes,
            uncertainty_hurdle_kes=uncertainty_hurdle,
            net_economic_benefit_of_cut_kes=net_benefit,
            recommend_rent_reduction=justified,
            rationale=rationale,
        )


# ======================================================================================
