"""
Construction Equipment Utilization, Breakdown Standby Reallocation,
and Capacitated Vehicle Routing Problem (CVRP) Engine.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Dict, List, Tuple
import math
import numpy as np
from ..scheduling import InfeasibleScheduleError


@dataclass(frozen=True)
class EquipmentSpec:
    equipment_id: str
    name: str
    equipment_type: str  # e.g., "Excavator", "Tower Crane", "Concrete Mixer", "Tipper Truck"
    capacity_per_day: float
    daily_ownership_or_hire_kes: float
    hourly_fuel_and_operator_kes: float
    mobilization_cost_kes: float
    availability_factor: float = 0.90  # Accounts for routine maintenance
    is_operational: bool = True


@dataclass(frozen=True)
class DeliverySiteNode:
    node_id: str
    name: str
    lat: float
    lon: float
    demand_tonnes: float


class EquipmentOptimizationEngine:
    """
    Optimizes heavy equipment selection, handles equipment breakdown contingency reallocation
    (Adversarial test #4), and solves Capacitated Vehicle Routing (CVRP) for site material haulage.
    """

    @staticmethod
    def allocate_equipment_with_breakdown_fallback(
        required_output_units: float,
        available_days: int,
        fleet: List[EquipmentSpec],
        hours_per_day: float = 8.0,
    ) -> Dict[str, object]:
        """
        Selects minimum-cost operational equipment combination to meet `required_output_units`
        within `available_days`, automatically excluding broken-down units (`is_operational=False`)
        and accounting for mechanical availability factor.
        """
        operational = [eq for eq in fleet if eq.is_operational]
        if not operational:
            raise InfeasibleScheduleError("Equipment breakdown crisis: Zero operational machines available in fleet.")

        # Rank by effective cost per unit of output over the window
        scored = []
        for eq in operational:
            eff_output = eq.capacity_per_day * eq.availability_factor * available_days
            total_cost = eq.mobilization_cost_kes + available_days * (
                eq.daily_ownership_or_hire_kes + hours_per_day * eq.hourly_fuel_and_operator_kes
            )
            cost_per_unit = total_cost / max(1e-9, eff_output)
            scored.append((cost_per_unit, eff_output, total_cost, eq))

        scored.sort(key=lambda x: x[0])
        selected = []
        accumulated_output = 0.0
        total_fleet_cost_kes = 0.0

        for _, eff_out, cost, eq in scored:
            if accumulated_output >= required_output_units - 1e-9:
                break
            selected.append(eq.equipment_id)
            accumulated_output += eff_out
            total_fleet_cost_kes += cost

        if accumulated_output < required_output_units - 1e-9:
            raise InfeasibleScheduleError(
                f"Equipment capacity shortage after breakdowns: Operational fleet can produce "
                f"{accumulated_output:.1f} units vs required {required_output_units:.1f} units in {available_days} days."
            )

        return {
            "selected_equipment_ids": selected,
            "total_effective_capacity": round(accumulated_output, 2),
            "required_output_units": required_output_units,
            "utilization_ratio": round(required_output_units / accumulated_output, 4),
            "total_equipment_cost_kes": round(total_fleet_cost_kes, 2),
        }

    @staticmethod
    def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        r = 6371.0
        phi1, phi2 = math.radians(lat1), math.radians(lat2)
        dphi = math.radians(lat2 - lat1)
        dlambda = math.radians(lon2 - lon1)
        a = math.sin(dphi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2.0) ** 2
        return 2.0 * r * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))

    @classmethod
    def solve_cvrp_clarke_wright(
        cls,
        depot: DeliverySiteNode,
        sites: List[DeliverySiteNode],
        truck_capacity_tonnes: float,
        cost_per_km_kes: float = 145.0,
    ) -> Dict[str, object]:
        """
        Solves the Capacitated Vehicle Routing Problem (CVRP) using the Clarke-Wright Parallel Savings
        Algorithm followed by intra-route 2-Opt local search refinement.
        """
        if truck_capacity_tonnes <= 0:
            raise ValueError("Truck capacity must be positive.")
        for s in sites:
            if s.demand_tonnes > truck_capacity_tonnes:
                raise ValueError(
                    f"Site '{s.node_id}' demand ({s.demand_tonnes}t) exceeds single truck capacity ({truck_capacity_tonnes}t)."
                )

        n = len(sites)
        if n == 0:
            return {"routes": [], "total_distance_km": 0.0, "total_transport_cost_kes": 0.0}

        dist_depot = [cls._haversine_km(depot.lat, depot.lon, s.lat, s.lon) for s in sites]
        dist_matrix = np.zeros((n, n), dtype=float)
        savings = []
        for i in range(n):
            for j in range(i + 1, n):
                d_ij = cls._haversine_km(sites[i].lat, sites[i].lon, sites[j].lat, sites[j].lon)
                dist_matrix[i, j] = dist_matrix[j, i] = d_ij
                sav = dist_depot[i] + dist_depot[j] - d_ij
                if sav > 0:
                    savings.append((sav, i, j))

        savings.sort(reverse=True, key=lambda x: x[0])

        # Initialize each site in its own route
        routes: List[List[int]] = [[i] for i in range(n)]
        route_loads: List[float] = [sites[i].demand_tonnes for i in range(n)]

        def _find_route(node_idx: int) -> Tuple[int, str]:
            for r_idx, r in enumerate(routes):
                if not r:
                    continue
                if r[0] == node_idx and len(r) == 1:
                    return r_idx, "SINGLE"
                if r[0] == node_idx:
                    return r_idx, "HEAD"
                if r[-1] == node_idx:
                    return r_idx, "TAIL"
            return -1, "INTERIOR"

        for _, i, j in savings:
            r_i, pos_i = _find_route(i)
            r_j, pos_j = _find_route(j)
            if r_i == -1 or r_j == -1 or r_i == r_j:
                continue
            if route_loads[r_i] + route_loads[r_j] > truck_capacity_tonnes + 1e-9:
                continue
            # Merge routes if i and j are exterior endpoints
            if pos_i in ("TAIL", "SINGLE") and pos_j in ("HEAD", "SINGLE"):
                routes[r_i].extend(routes[r_j])
                route_loads[r_i] += route_loads[r_j]
                routes[r_j] = []
            elif pos_j in ("TAIL", "SINGLE") and pos_i in ("HEAD", "SINGLE"):
                routes[r_j].extend(routes[r_i])
                route_loads[r_j] += route_loads[r_i]
                routes[r_i] = []

        # 2-Opt refinement on each active route
        final_routes = []
        total_km = 0.0
        for r_idx, r in enumerate(routes):
            if not r:
                continue
            best_r = list(r)

            def _route_dist(seq: List[int]) -> float:
                d = dist_depot[seq[0]] + dist_depot[seq[-1]]
                for k in range(len(seq) - 1):
                    d += dist_matrix[seq[k], seq[k + 1]]
                return d

            best_d = _route_dist(best_r)
            improved = True
            while improved and len(best_r) >= 3:
                improved = False
                for a in range(len(best_r) - 1):
                    for b in range(a + 2, len(best_r) + 1):
                        cand = best_r[:a] + list(reversed(best_r[a:b])) + best_r[b:]
                        cand_d = _route_dist(cand)
                        if cand_d < best_d - 1e-6:
                            best_r = cand
                            best_d = cand_d
                            improved = True
            total_km += best_d
            final_routes.append({
                "stop_sequence": [depot.node_id] + [sites[idx].node_id for idx in best_r] + [depot.node_id],
                "total_load_tonnes": round(route_loads[r_idx], 2),
                "distance_km": round(best_d, 2),
                "route_cost_kes": round(best_d * cost_per_km_kes, 2),
            })

        return {
            "num_vehicles_used": len(final_routes),
            "total_distance_km": round(total_km, 2),
            "total_transport_cost_kes": round(total_km * cost_per_km_kes, 2),
            "routes": final_routes,
        }
