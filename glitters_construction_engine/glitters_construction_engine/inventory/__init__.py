"""
Stochastic Construction Inventory, Economic Order Quantity (EOQ),
Reorder Point (R, Q), Safety Stock, and Multi-Echelon Supply Chain Engine.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple
import math
from scipy import stats


@dataclass(frozen=True)
class PriceBreakTier:
    min_quantity: float
    max_quantity: float
    unit_price_kes: float


class InventoryOptimizationEngine:
    """
    Implements:
    1. Classical Wilson EOQ and All-Units Quantity Discount EOQ.
    2. Stochastic Continuous-Review (Q, R) Inventory Policy with joint demand and lead-time uncertainty:
       sigma_{DDLT} = sqrt( E[L] * Var(D) + (E[D])^2 * Var(L) )
       Safety Stock SS = z_{alpha} * sigma_{DDLT}
       Reorder Point ROP = E[D] * E[L] + SS
    3. Multi-Echelon Inventory (Central Yard -> County Depot -> Construction Site) via Clark-Scarf echelon stock.
    """

    @staticmethod
    def compute_eoq_and_reorder_policy(
        annual_or_project_demand: float,
        horizon_days: int,
        ordering_cost_per_order_kes: float,
        holding_cost_per_unit_per_day_kes: float,
        mean_daily_demand: float,
        std_daily_demand: float,
        mean_lead_time_days: float,
        std_lead_time_days: float,
        service_level_alpha: float = 0.95,
        price_tiers: Optional[List[PriceBreakTier]] = None,
    ) -> Dict[str, object]:
        if annual_or_project_demand <= 0 or horizon_days <= 0 or holding_cost_per_unit_per_day_kes <= 0:
            raise ValueError("Demand, horizon, and holding cost must be strictly positive.")
        if not (0.50 <= service_level_alpha < 1.0):
            raise ValueError("Service level alpha must be in [0.50, 1.0).")

        holding_cost_horizon = holding_cost_per_unit_per_day_kes * horizon_days
        raw_eoq = math.sqrt((2.0 * annual_or_project_demand * ordering_cost_per_order_kes) / holding_cost_horizon)

        best_q = raw_eoq
        best_unit_price = 0.0
        if price_tiers:
            best_total_cost = float("inf")
            for tier in price_tiers:
                q_cand = max(tier.min_quantity, min(raw_eoq, tier.max_quantity))
                n_orders = annual_or_project_demand / q_cand
                tc = (
                    annual_or_project_demand * tier.unit_price_kes
                    + n_orders * ordering_cost_per_order_kes
                    + (q_cand / 2.0) * holding_cost_horizon
                )
                if tc < best_total_cost:
                    best_total_cost = tc
                    best_q = q_cand
                    best_unit_price = tier.unit_price_kes

        # Joint Demand During Lead Time (DDLT) distribution
        expected_ddlt = mean_daily_demand * mean_lead_time_days
        var_ddlt = (mean_lead_time_days * (std_daily_demand ** 2)) + ((mean_daily_demand ** 2) * (std_lead_time_days ** 2))
        std_ddlt = math.sqrt(max(0.0, var_ddlt))
        z_alpha = float(stats.norm.ppf(service_level_alpha))
        safety_stock = z_alpha * std_ddlt
        reorder_point = expected_ddlt + safety_stock

        total_ordering_kes = (annual_or_project_demand / best_q) * ordering_cost_per_order_kes
        cycle_holding_kes = (best_q / 2.0) * holding_cost_horizon
        safety_holding_kes = safety_stock * holding_cost_horizon

        return {
            "optimal_order_quantity_eoq": round(best_q, 2),
            "selected_unit_price_kes": round(best_unit_price, 2),
            "expected_demand_during_lead_time": round(expected_ddlt, 2),
            "std_dev_demand_during_lead_time": round(std_ddlt, 2),
            "service_level_alpha": service_level_alpha,
            "z_score": round(z_alpha, 4),
            "safety_stock_units": round(safety_stock, 2),
            "reorder_point_units": round(reorder_point, 2),
            "total_ordering_cost_kes": round(total_ordering_kes, 2),
            "cycle_holding_cost_kes": round(cycle_holding_kes, 2),
            "safety_stock_holding_cost_kes": round(safety_holding_kes, 2),
            "total_inventory_ logistics_cost_kes": round(total_ordering_kes + cycle_holding_kes + safety_holding_kes, 2),
        }

    @staticmethod
    def optimize_multi_echelon_safety_stock(
        central_depot_lead_time_days: float,
        site_transit_lead_times_days: Dict[str, float],
        site_daily_demand_means: Dict[str, float],
        site_daily_demand_stds: Dict[str, float],
        service_level_alpha: float = 0.95,
    ) -> Dict[str, object]:
        """
        Computes Risk-Pooling Multi-Echelon Inventory (Central Nairobi Yard supplying multiple
        County Construction Sites) vs Independent Decentralized Site Inventories (Eppen's Risk Pooling Theorem).
        """
        z = float(stats.norm.ppf(service_level_alpha))
        decentralized_ss_total = 0.0
        site_local_ss: Dict[str, float] = {}

        for site_id, mu_i in site_daily_demand_means.items():
            sigma_i = site_daily_demand_stds[site_id]
            l_local = site_transit_lead_times_days[site_id]
            # If decentralized, each site buffers against (central_lead_time + transit_lead_time)
            ss_indep = z * sigma_i * math.sqrt(central_depot_lead_time_days + l_local)
            decentralized_ss_total += ss_indep
            # Under echelon risk pooling, site buffers local transit L_i, central yard pools variance across sites
            site_local_ss[site_id] = round(z * sigma_i * math.sqrt(l_local), 2)

        pooled_daily_var = sum(s ** 2 for s in site_daily_demand_stds.values())
        central_pooled_ss = z * math.sqrt(pooled_daily_var * central_depot_lead_time_days)
        total_echelon_ss = central_pooled_ss + sum(site_local_ss.values())
        pooling_reduction_pct = (
            (decentralized_ss_total - total_echelon_ss) / decentralized_ss_total
            if decentralized_ss_total > 0 else 0.0
        )

        return {
            "decentralized_total_safety_stock": round(decentralized_ss_total, 2),
            "central_yard_pooled_safety_stock": round(central_pooled_ss, 2),
            "site_local_safety_stocks": site_local_ss,
            "multi_echelon_total_safety_stock": round(total_echelon_ss, 2),
            "risk_pooling_inventory_reduction_pct": round(pooling_reduction_pct * 100.0, 2),
        }
