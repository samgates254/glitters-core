from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Optional

from ..domain.entities import PropertyAsset
from ..domain.enums import AssetCategory, GeographicSubmarket


@dataclass(frozen=True, slots=True)
class UpstreamEngineDataFeed:
    """
    Explicitly typed upstream data snapshot representing available or missing upstream engine feeds.
    Strictly documents engine availability flags and audit provenance.
    """
    asset_id: str
    valuation_cents: Optional[int] = None
    valuation_source: str = "DEFAULT_OR_MANUAL"
    clearing_rent_monthly_cents: Optional[int] = None
    clearing_rent_source: str = "DEFAULT_OR_MANUAL"
    projected_vacancy_rate: Optional[float] = None
    planned_renovation_capex_cents: Optional[int] = None
    planned_maintenance_cents: Optional[int] = None
    spatial_risk_multiplier: float = 1.0
    is_engine02_available: bool = False  # Valuation Engine
    is_engine03_available: bool = False  # Geospatial Intelligence Engine
    is_engine04_available: bool = False  # Rental Optimization Engine
    is_engine05_available: bool = False  # Construction Engine
    is_engine06_available: bool = False  # Financial Intelligence Engine
    is_engine10_available: bool = False  # Predictive Maintenance Engine

    def __post_init__(self) -> None:
        if self.valuation_cents is not None and self.valuation_cents < 0:
            raise ValueError(f"valuation_cents cannot be negative: {self.valuation_cents}")
        if self.clearing_rent_monthly_cents is not None and self.clearing_rent_monthly_cents < 0:
            raise ValueError(f"clearing_rent_monthly_cents cannot be negative: {self.clearing_rent_monthly_cents}")
        if self.projected_vacancy_rate is not None:
            if not math.isfinite(self.projected_vacancy_rate) or not (0.0 <= self.projected_vacancy_rate <= 1.0):
                raise ValueError(f"projected_vacancy_rate must be in [0, 1]: {self.projected_vacancy_rate}")
        if self.planned_renovation_capex_cents is not None and self.planned_renovation_capex_cents < 0:
            raise ValueError(f"planned_renovation_capex_cents cannot be negative: {self.planned_renovation_capex_cents}")
        if self.planned_maintenance_cents is not None and self.planned_maintenance_cents < 0:
            raise ValueError(f"planned_maintenance_cents cannot be negative: {self.planned_maintenance_cents}")
        if not math.isfinite(self.spatial_risk_multiplier) or self.spatial_risk_multiplier <= 0.0:
            raise ValueError(f"spatial_risk_multiplier must be a positive finite float: {self.spatial_risk_multiplier}")


class UpstreamIntegrationAdapter:
    """
    Typed adapter converting upstream engine outputs into PropertyAsset definitions.
    Strictly documents and manages missing or unintegrated upstream engines.
    Applies spatial risk multipliers and routes predictive maintenance via Engine 10.
    """

    @classmethod
    def apply_upstream_feed(
        cls,
        base_asset: PropertyAsset,
        feed: UpstreamEngineDataFeed,
    ) -> PropertyAsset:
        """
        Applies available upstream estimates to refine asset investment state.
        Strictly rejects feeds where asset_id does not match base_asset.
        Respects availability flags; unflagged feeds fall back safely to base asset values.
        """
        if feed.asset_id != base_asset.asset_id:
            raise ValueError(
                f"Asset ID mismatch: feed.asset_id '{feed.asset_id}' does not match base_asset.asset_id '{base_asset.asset_id}'"
            )

        # Engine 02: Valuation
        val = (
            feed.valuation_cents
            if (feed.is_engine02_available and feed.valuation_cents is not None)
            else base_asset.current_valuation_cents
        )

        # Engine 04: Clearing Rent
        rent = (
            feed.clearing_rent_monthly_cents
            if (feed.is_engine04_available and feed.clearing_rent_monthly_cents is not None)
            else base_asset.monthly_gross_potential_rent_cents
        )

        # Engine 03: Geospatial Vacancy & Spatial Risk Multiplier
        if feed.is_engine03_available:
            base_vac = feed.projected_vacancy_rate if feed.projected_vacancy_rate is not None else base_asset.annual_baseline_vacancy_rate
            vac = min(1.0, max(0.0, base_vac * feed.spatial_risk_multiplier))
        else:
            vac = base_asset.annual_baseline_vacancy_rate

        # Engine 05: Construction Planned Renovation CapEx
        capex = (
            feed.planned_renovation_capex_cents
            if (feed.is_engine05_available and feed.planned_renovation_capex_cents is not None)
            else base_asset.planned_capex_cents
        )

        # Engine 10: Predictive Maintenance (Engine 06 is Financial Intelligence, not maintenance)
        maint = (
            feed.planned_maintenance_cents
            if (feed.is_engine10_available and feed.planned_maintenance_cents is not None)
            else base_asset.annual_routine_maintenance_cents
        )

        return PropertyAsset(
            asset_id=base_asset.asset_id,
            name=base_asset.name,
            category=base_asset.category,
            submarket=base_asset.submarket,
            acquisition_valuation_cents=base_asset.acquisition_valuation_cents,
            current_valuation_cents=val,
            monthly_gross_potential_rent_cents=rent,
            monthly_operating_expense_cents=base_asset.monthly_operating_expense_cents,
            annual_baseline_vacancy_rate=vac,
            annual_baseline_credit_loss_rate=base_asset.annual_baseline_credit_loss_rate,
            annual_debt_service_cents=base_asset.annual_debt_service_cents,
            current_loan_balance_cents=base_asset.current_loan_balance_cents,
            annual_routine_maintenance_cents=maint,
            planned_capex_cents=capex,
            acquisition_cost_rate=base_asset.acquisition_cost_rate,
            disposal_cost_rate=base_asset.disposal_cost_rate,
            renovation_downtime_months=base_asset.renovation_downtime_months,
            liquidity_discount_rate=base_asset.liquidity_discount_rate,
            minimum_holding_months=base_asset.minimum_holding_months,
            initial_ownership_fraction=base_asset.initial_ownership_fraction,
        )
