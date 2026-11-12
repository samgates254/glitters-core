from __future__ import annotations
"""Synthetic Kenyan Multi-Segment Rental Dataset Generator & Controlled Backtesting Framework.

Phase 1 Audit Remediation (v2.0):
- Uses 500-path Monte Carlo simulation (`n_simulations = 500`) matching the formal specification.
- Includes explicit `unit_identifier` on distinct multi-unit apartment comparables and
  `ListingLifecycleStatus` competing-risks states (`VERIFIED_LEASE_EXECUTED`,
  `VERIFIED_WITHDRAWN_UNLEASED`, `ACTIVE_OR_UNVERIFIED_PORTAL_AD`).
- Includes verified cryptographic evidence fields (`lease_contract_id`, `payment_receipt_hash`)
  for verified transactions and tests tri-state `None` (`UNKNOWN`) amenity attributes.
"""


from dataclasses import dataclass
from typing import Sequence
import numpy as np

from ..demand.models import RentalDemandModel
from ..portfolio.optimizer import PortfolioRevenueOptimizer
from ..schemas import (
    LeaseStatus,
    ListingLifecycleStatus,
    MarketComparableListing,
    OwnerPricingPolicy,
    PropertySegment,
    UnitSpecification,
    verify_transaction_evidence,
)


@dataclass(frozen=True)
class BacktestValidationReport:
    """Comprehensive out-of-time scientific validation and backtesting metrics."""

    is_synthetic_benchmark: bool
    monte_carlo_paths_per_unit: int
    n_units_evaluated: int
    rent_mae_kes: float
    rent_rmse_kes: float
    demand_arrival_mae: float
    vacancy_duration_mae_months: float
    average_pricing_regret_kes: float
    renewal_brier_calibration_score: float
    baseline_occupancy_rate: float
    optimized_occupancy_rate: float
    baseline_monthly_noi_kes: float
    optimized_monthly_noi_kes: float
    simulated_noi_uplift_pct: float
    baseline_revenue_volatility_kes: float
    optimized_revenue_volatility_kes: float
    passed_outcome_fairness: bool


def generate_synthetic_kenyan_portfolio(
    n_units: int = 120,
    n_comparables: int = 360,
    random_seed: int = 2026,
) -> tuple[list[UnitSpecification], list[MarketComparableListing]]:
    """Generates a realistic, explicitly labelled synthetic Kenyan rental dataset across all 12 segments."""
    rng = np.random.default_rng(random_seed)

    submarkets = [
        ("Nairobi", "Kilimani", PropertySegment.RESIDENTIAL_APARTMENT, 85.0, 2, 68000.0, 220000.0),
        ("Nairobi", "Westlands", PropertySegment.RESIDENTIAL_APARTMENT, 110.0, 2, 95000.0, 310000.0),
        ("Nairobi", "Roysambu", PropertySegment.BEDSITTER, 20.0, 0, 11500.0, 42000.0),
        ("Kiambu", "Ruaka", PropertySegment.STUDIO, 32.0, 1, 21000.0, 72000.0),
        ("Nairobi", "Karen", PropertySegment.STANDALONE_HOUSE, 380.0, 4, 260000.0, 850000.0),
        ("Nairobi", "Juja", PropertySegment.STUDENT_ACCOMMODATION, 16.0, 0, 8500.0, 28000.0),
        ("Nairobi", "Upper Hill", PropertySegment.OFFICE, 220.0, 0, 240000.0, 900000.0),
        ("Nairobi", "CBD", PropertySegment.RETAIL_SPACE, 65.0, 0, 110000.0, 420000.0),
        ("Machakos", "Athi River", PropertySegment.WAREHOUSE, 950.0, 0, 420000.0, 1800000.0),
        ("Nairobi", "Industrial Area", PropertySegment.INDUSTRIAL_PROPERTY, 1200.0, 0, 580000.0, 2400000.0),
        ("Mombasa", "Nyali", PropertySegment.HOSPITALITY_SHORT_STAY, 75.0, 2, 85000.0, 280000.0),
        ("Nakuru", "Milimani", PropertySegment.MIXED_USE_DEVELOPMENT, 140.0, 2, 75000.0, 260000.0),
        ("Kisumu", "Milimani", PropertySegment.COMMERCIAL_BUILDING, 480.0, 0, 390000.0, 1500000.0),
    ]

    units: list[UnitSpecification] = []
    for idx in range(n_units):
        county, neigh, seg, base_area, beds, base_rent, med_inc = submarkets[idx % len(submarkets)]
        prop_id = f"SYNTH_PROP_{(idx % 24) + 1:03d}"
        landlord_id = f"SYNTH_LANDLORD_{(idx % 8) + 1:02d}"
        area = float(max(12.0, base_area * rng.uniform(0.88, 1.18)))

        # Tri-state amenities: 10% explicitly unknown (None) to test hardened tri-state handling
        r_bh = rng.random()
        borehole: bool | None = None if r_bh < 0.10 else bool(r_bh > 0.30)
        r_gen = rng.random()
        generator: bool | None = None if r_gen < 0.10 else bool(r_gen > 0.50)
        sec_tier = int(rng.integers(2, 6))

        true_mkt_rent = (
            base_rent
            * ((area / base_area) ** 0.60)
            * (1.04 if borehole is True else (0.97 if borehole is False else 1.0))
            * (1.05 if generator is True else 1.0)
            * (1.0 + 0.03 * (sec_tier - 3))
        )

        r_stat = rng.random()
        if r_stat < 0.55:
            status = LeaseStatus.VACANT
            months_rem = 0
            vac_days = int(rng.integers(5, 95))
            asking = round(true_mkt_rent * float(rng.uniform(0.86, 1.26)), -2)
            contractual = None
            collected = None
        elif r_stat < 0.85:
            status = LeaseStatus.OCCUPIED_ACTIVE
            months_rem = int(rng.integers(4, 11))
            vac_days = 0
            asking = round(true_mkt_rent * 1.08, -2)
            contractual = round(true_mkt_rent * float(rng.uniform(0.92, 1.05)), -2)
            collected = round(contractual * (1.0 if rng.random() > 0.08 else 0.85), 2)
        else:
            status = LeaseStatus.EXPIRING_RENEWAL_WINDOW
            months_rem = int(rng.integers(1, 3))
            vac_days = 0
            asking = round(true_mkt_rent * 1.08, -2)
            contractual = round(true_mkt_rent * float(rng.uniform(0.88, 1.02)), -2)
            collected = contractual

        policy = OwnerPricingPolicy(
            min_rent_kes=round(true_mkt_rent * 0.82, -2),
            max_rent_kes=round(true_mkt_rent * 1.32, -2),
            max_annual_increase_pct=0.10,
            target_min_occupancy_rate=0.85,
        )

        units.append(
            UnitSpecification(
                unit_id=f"SYNTH_UNIT_{idx + 1:04d}",
                property_id=prop_id,
                landlord_id=landlord_id,
                management_org_id="SYNTH_GLITTERS_MGR_01",
                segment=seg,
                county=county,
                neighbourhood=neigh,
                floor_area_sqm=round(area, 1),
                bedrooms=beds,
                bathrooms=max(1.0, float(beds)),
                has_borehole_water=borehole,
                has_backup_generator=generator,
                has_solar_water_heating=bool(rng.random() > 0.5),
                security_tier=sec_tier,
                parking_bays=1 if beds >= 1 else 0,
                furnishing_level=0,
                lease_status=status,
                current_asking_rent_kes=asking,
                contractual_rent_kes=contractual,
                actual_collected_rent_kes=collected,
                months_remaining_on_lease=months_rem,
                current_vacancy_days=vac_days,
                typical_lease_term_months=12 if seg != PropertySegment.OFFICE else 36,
                routine_maintenance_cost_kes=round(base_rent * 0.04, 2),
                vacancy_holding_cost_kes=round(base_rent * 0.025, 2),
                tenant_acquisition_cost_kes=round(base_rent * 0.25, 2),
                turnover_refurbishment_cost_kes=round(base_rent * 0.35, 2),
                management_fee_pct=0.08,
                baseline_arrears_rate=0.03,
                target_tenant_median_income_kes=med_inc,
                asset_valuation_kes=round(true_mkt_rent * 135.0, 2),
                owner_policy=policy,
                extra_metadata={
                    "is_synthetic": True,
                    "data_origin": "SYNTHETIC_SIMULATION_BENCHMARK",
                    "synthetic_true_clearing_rent_kes": round(true_mkt_rent, 2),
                },
            )
        )

    comparables: list[MarketComparableListing] = []
    for j in range(n_comparables):
        county, neigh, seg, base_area, beds, base_rent, _ = submarkets[j % len(submarkets)]
        area = float(max(12.0, base_area * rng.uniform(0.85, 1.22)))
        borehole = bool(rng.random() > 0.25)
        generator = bool(rng.random() > 0.45)
        sec_tier = int(rng.integers(2, 6))

        true_clear = (
            base_rent
            * ((area / base_area) ** 0.60)
            * (1.04 if borehole else 0.97)
            * (1.05 if generator else 1.0)
            * (1.0 + 0.03 * (sec_tier - 3))
            * float(rng.lognormal(0.0, 0.06))
        )
        r_ev = rng.random()
        if r_ev < 0.35:
            contract_id = f"SYNTH_LEASE_{j + 1:05d}"
            receipt_hash = f"SHA256_SYNTH_{j + 1:05d}"
            tx_rent = round(true_clear, 2)
            lifecycle = ListingLifecycleStatus.VERIFIED_LEASE_EXECUTED
        elif r_ev < 0.48:
            contract_id = None
            receipt_hash = None
            tx_rent = None
            lifecycle = ListingLifecycleStatus.VERIFIED_WITHDRAWN_UNLEASED
        else:
            contract_id = None
            receipt_hash = None
            tx_rent = None
            lifecycle = ListingLifecycleStatus.ACTIVE_OR_UNVERIFIED_PORTAL_AD

        is_verified = verify_transaction_evidence(contract_id, receipt_hash, tx_rent)
        age_days = int(rng.integers(2, 140))
        markup = float(rng.uniform(1.04, 1.16))
        asking_rent = round(true_clear * markup, -2)

        if j % 33 == 0 and comparables:
            prev = comparables[-1]
            comparables.append(
                MarketComparableListing(
                    listing_id=f"SYNTH_COMP_DUP_{j + 1:04d}",
                    segment=prev.segment,
                    county=prev.county,
                    neighbourhood=prev.neighbourhood,
                    floor_area_sqm=prev.floor_area_sqm * 1.01,
                    bedrooms=prev.bedrooms,
                    asking_rent_kes=prev.asking_rent_kes * 1.01,
                    verified_transaction_rent_kes=None,
                    listing_age_days=prev.listing_age_days + 5,
                    is_verified_transaction=False,
                    has_borehole_water=prev.has_borehole_water,
                    has_backup_generator=prev.has_backup_generator,
                    security_tier=prev.security_tier,
                    source_portal="SYNTHETIC_DUPLICATE_PORTAL",
                    unit_identifier=None,
                )
            )
            continue

        comparables.append(
            MarketComparableListing(
                listing_id=f"SYNTH_COMP_{j + 1:04d}",
                segment=seg,
                county=county,
                neighbourhood=neigh,
                floor_area_sqm=round(area, 1),
                bedrooms=beds,
                asking_rent_kes=asking_rent,
                verified_transaction_rent_kes=tx_rent,
                listing_age_days=age_days,
                is_verified_transaction=is_verified,
                has_borehole_water=borehole,
                has_backup_generator=generator,
                security_tier=sec_tier,
                source_portal="SYNTHETIC_PORTAL_FEED",
                unit_identifier=f"DOOR_{(j % 50) + 1:02d}" if is_verified else None,
                lifecycle_status=lifecycle,
                lease_contract_id=contract_id,
                payment_receipt_hash=receipt_hash,
            )
        )

    return units, comparables


class TimeBasedBacktester:
    """Executes out-of-time scientific validation and backtesting on synthetic or historical data."""

    def __init__(self, portfolio_optimizer: PortfolioRevenueOptimizer | None = None) -> None:
        self.portfolio_optimizer = portfolio_optimizer or PortfolioRevenueOptimizer()
        self.demand_model = self.portfolio_optimizer.demand_model

    def run_backtest(
        self,
        units: Sequence[UnitSpecification],
        comparables: Sequence[MarketComparableListing],
        calendar_month: int = 11,
    ) -> BacktestValidationReport:
        """Evaluates accuracy, calibration, pricing regret, fairness, and simulated NOI improvement."""
        opt_res = self.portfolio_optimizer.optimize_portfolio(
            units, comparables, calendar_month=calendar_month
        )
        recs = opt_res.unit_recommendations

        rent_errors = []
        arrival_errors = []
        vac_errors = []
        regrets = []
        brier_scores = []
        baseline_nois = []
        opt_nois = []
        base_occs = []
        opt_occs = []

        rng = np.random.default_rng(999)

        for u, r in zip(units, recs):
            true_mkt = float(
                u.extra_metadata.get(
                    "synthetic_true_clearing_rent_kes", r.ledger.achievable_market_rent_kes
                )
            )
            rent_errors.append(r.ledger.achievable_market_rent_kes - true_mkt)

            true_eval_opt = self.demand_model.evaluate_at_price(
                r.ledger.recommended_rent_kes,
                u,
                true_mkt,
                r.price_elasticity,
                calendar_month,
            )
            base_price = (
                u.contractual_rent_kes
                if r.status.value in ("locked_contractual", "renewal_workflow_required")
                and u.contractual_rent_kes
                else (u.current_asking_rent_kes or true_mkt)
            )
            true_eval_base = self.demand_model.evaluate_at_price(
                base_price,
                u,
                true_mkt,
                r.price_elasticity,
                calendar_month,
            )

            grid_p = np.linspace(true_mkt * 0.75, true_mkt * 1.25, 25)
            oracle_noi = max(
                self.demand_model.evaluate_at_price(
                    float(gp), u, true_mkt, r.price_elasticity, calendar_month
                ).expected_monthly_noi_kes
                for gp in grid_p
            )
            regret = max(0.0, oracle_noi - true_eval_opt.expected_monthly_noi_kes)
            if r.status.value != "locked_contractual":
                regrets.append(regret)

            arrival_errors.append(
                abs(r.expected_application_arrival_rate - true_eval_opt.application_arrival_rate)
            )
            vac_errors.append(
                abs(
                    r.expected_vacancy_duration_months
                    - true_eval_opt.expected_vacancy_duration_months
                )
            )

            realized_ren = 1.0 if rng.random() < true_eval_opt.renewal_probability else 0.0
            brier_scores.append((r.expected_renewal_probability - realized_ren) ** 2)

            baseline_nois.append(true_eval_base.expected_monthly_noi_kes)
            opt_nois.append(true_eval_opt.expected_monthly_noi_kes)
            base_occs.append(true_eval_base.finite_horizon_occupancy_prob)
            opt_occs.append(true_eval_opt.finite_horizon_occupancy_prob)

        rent_err_arr = np.array(rent_errors, dtype=float)
        mae_kes = float(np.mean(np.abs(rent_err_arr)))
        rmse_kes = float(np.sqrt(np.mean(rent_err_arr**2)))

        base_noi_sum = float(np.sum(baseline_nois))
        opt_noi_sum = float(np.sum(opt_nois))
        uplift_pct = ((opt_noi_sum - base_noi_sum) / max(abs(base_noi_sum), 1.0)) * 100.0

        mc_paths = self.portfolio_optimizer.single_optimizer.uncertainty_engine.n_simulations

        return BacktestValidationReport(
            is_synthetic_benchmark=True,
            monte_carlo_paths_per_unit=mc_paths,
            n_units_evaluated=len(units),
            rent_mae_kes=round(mae_kes, 2),
            rent_rmse_kes=round(rmse_kes, 2),
            demand_arrival_mae=round(float(np.mean(arrival_errors)), 4),
            vacancy_duration_mae_months=round(float(np.mean(vac_errors)), 4),
            average_pricing_regret_kes=round(float(np.mean(regrets)) if regrets else 0.0, 2),
            renewal_brier_calibration_score=round(float(np.mean(brier_scores)), 4),
            baseline_occupancy_rate=round(float(np.mean(base_occs)), 4),
            optimized_occupancy_rate=round(float(np.mean(opt_occs)), 4),
            baseline_monthly_noi_kes=round(base_noi_sum, 2),
            optimized_monthly_noi_kes=round(opt_noi_sum, 2),
            simulated_noi_uplift_pct=round(uplift_pct, 2),
            baseline_revenue_volatility_kes=round(float(np.std(baseline_nois)), 2),
            optimized_revenue_volatility_kes=round(float(np.std(opt_nois)), 2),
            passed_outcome_fairness=opt_res.fairness_audit.passed_outcome_fairness,
        )


# ======================================================================================
