"""
Construction Risk, Uncertainty, Stochastic Programming, Bayesian Risk Network,
Critical-Chain Buffer Management, and Monte Carlo Simulation Engine.
Never presents uncertain estimates as guaranteed outcomes; always outputs explicit
probability distributions and confidence intervals (P10, P50, P80, P90, P95).
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Dict, List, Optional
import math
import numpy as np
from ..project_model import ConstructionProject, DependencyType
from ..critical_path import CriticalPathEngine


@dataclass(frozen=True)
class RiskFactorProbabilities:
    """
    Prior and conditional probabilities for the Construction Bayesian Risk Network.
    """
    p_weather_disruption: float = 0.25       # Kenyan Long/Short rains disruption probability
    p_supplier_delay: float = 0.20           # Import/local haulage delay probability
    p_equipment_breakdown: float = 0.12      # Mechanical failure probability
    p_design_change: float = 0.15            # Client/architect variation order probability
    p_price_spike: float = 0.22              # Steel/cement/fuel price volatility probability


class RiskAndUncertaintyEngine:
    """
    Mathematical Risk Engine providing:
    1. Vectorized Monte Carlo Schedule & Cost Network Simulation (Beta-PERT + Lognormal price shocks + Rework).
    2. Exact Discrete Bayesian Network (DAG) joint and posterior risk propagation.
    3. Critical-Chain Project Management (CCPM) Project & Feeding Buffer sizing (Root Square Error Method).
    4. Bertsimas-Sim Gamma-Robust Budget & Schedule Protection.
    """

    @staticmethod
    def run_monte_carlo_simulation(
        project: ConstructionProject,
        num_iterations: int = 2000,
        random_seed: int = 42,
        material_price_volatility_cv: float = 0.10,
        weather_delay_prob: float = 0.15,
        rework_prob_per_activity: float = 0.05,
    ) -> Dict[str, object]:
        """
        Executes `num_iterations` vectorized Monte Carlo trials over the project precedence network.
        Samples activity durations from 4-parameter Beta-PERT distributions, incorporates weather shocks
        and rework occurrences, and computes empirical P10, P50, P80, P90, P95 cost and completion distributions,
        plus Activity Criticality Index (CI), Cruciality Index (CRI), and Schedule Sensitivity Index (SSI).
        """
        if num_iterations < 100:
            raise ValueError("Monte Carlo simulation requires at least 100 iterations for statistical validity.")

        rng = np.random.default_rng(random_seed)
        topo = CriticalPathEngine.topological_sort_and_validate(project)
        n_act = len(topo)
        idx_map = {aid: i for i, aid in enumerate(topo)}

        # Sample duration matrix D of shape (num_iterations, n_act)
        D = np.zeros((num_iterations, n_act), dtype=float)
        C_direct = np.zeros((num_iterations, n_act), dtype=float)

        for i, aid in enumerate(topo):
            act = project.activities[aid]
            a = act.optimistic_days if act.optimistic_days is not None else act.duration_days * 0.85
            m = act.most_likely_days if act.most_likely_days is not None else float(act.duration_days)
            b = act.pessimistic_days if act.pessimistic_days is not None else act.duration_days * 1.40

            if b > a + 1e-6:
                alpha = 1.0 + 4.0 * (m - a) / (b - a)
                beta_param = 1.0 + 4.0 * (b - m) / (b - a)
                samples = a + rng.beta(alpha, beta_param, size=num_iterations) * (b - a)
            else:
                samples = np.full(num_iterations, float(act.duration_days))

            # Add stochastic rework shock (if rework triggers, adds 30% duration & 25% cost)
            rework_hits = rng.random(num_iterations) < rework_prob_per_activity
            weather_hits = rng.random(num_iterations) < weather_delay_prob
            D[:, i] = samples * (1.0 + 0.30 * rework_hits + 0.15 * weather_hits)

            # Lognormal material/direct cost shock
            if act.normal_cost_kes > 0:
                sigma_ln = math.sqrt(math.log(1.0 + material_price_volatility_cv ** 2))
                mu_ln = -0.5 * (sigma_ln ** 2)
                price_mult = rng.lognormal(mean=mu_ln, sigma=sigma_ln, size=num_iterations)
                C_direct[:, i] = act.normal_cost_kes * price_mult * (1.0 + 0.25 * rework_hits)

        # Vectorized forward pass across all Monte Carlo trials simultaneously
        ES = np.zeros((num_iterations, n_act), dtype=float)
        EF = np.zeros((num_iterations, n_act), dtype=float)
        preds = {i: [] for i in range(n_act)}
        succs = {i: [] for i in range(n_act)}
        for dep in project.dependencies:
            u, v = idx_map[dep.predecessor_id], idx_map[dep.successor_id]
            preds[v].append((u, dep.dep_type, dep.lag_days))
            succs[u].append((v, dep.dep_type, dep.lag_days))

        for i, aid in enumerate(topo):
            act = project.activities[aid]
            earliest = np.full(num_iterations, float(act.earliest_start_window))
            for u, dtype, lag in preds[i]:
                if dtype == DependencyType.FS:
                    cand = EF[:, u] + lag
                elif dtype == DependencyType.SS:
                    cand = ES[:, u] + lag
                elif dtype == DependencyType.FF:
                    cand = EF[:, u] + lag - D[:, i]
                else:
                    cand = ES[:, u] + lag - D[:, i]
                earliest = np.maximum(earliest, cand)
            ES[:, i] = earliest
            EF[:, i] = earliest + D[:, i]

        T_end = np.max(EF, axis=1)

        # Vectorized backward pass to identify critical activities per trial
        LS = np.zeros((num_iterations, n_act), dtype=float)
        LF = np.zeros((num_iterations, n_act), dtype=float)
        for i in reversed(range(n_act)):
            latest = T_end.copy()
            for v, dtype, lag in succs[i]:
                if dtype == DependencyType.FS:
                    cand = LS[:, v] - lag
                elif dtype == DependencyType.SS:
                    cand = LS[:, v] - lag + D[:, i]
                elif dtype == DependencyType.FF:
                    cand = LF[:, v] - lag
                else:
                    cand = LF[:, v] - lag + D[:, i]
                latest = np.minimum(latest, cand)
            LF[:, i] = latest
            LS[:, i] = latest - D[:, i]

        TF = LS - ES
        is_crit_matrix = np.abs(TF) < 1e-5
        criticality_indices = np.mean(is_crit_matrix, axis=0)

        # Total trial cost = direct cost + overhead + liquidated damages
        delays = np.maximum(0.0, T_end - project.contractual_deadline_days)
        total_costs = (
            np.sum(C_direct, axis=1)
            + T_end * project.daily_overhead_kes
            + delays * project.liquidated_damages_per_day_kes
        )

        std_T = float(np.std(T_end))
        sensitivity_metrics: Dict[str, Dict[str, float]] = {}
        for i, aid in enumerate(topo):
            ci = float(criticality_indices[i])
            std_di = float(np.std(D[:, i]))
            if std_T > 1e-9 and std_di > 1e-9:
                corr = float(np.corrcoef(D[:, i], T_end)[0, 1])
                ssi = ci * (std_di / std_T)
            else:
                corr = 0.0
                ssi = 0.0
            sensitivity_metrics[aid] = {
                "criticality_index": round(ci, 4),
                "cruciality_index_corr": round(abs(corr), 4),
                "schedule_sensitivity_index": round(ssi, 4),
            }

        return {
            "project_id": project.project_id,
            "is_guaranteed_outcome": False,
            "uncertainty_disclaimer": "Stochastic Monte Carlo distribution; never treat percentile estimates as deterministic guarantees.",
            "num_iterations": num_iterations,
            "duration_distribution_days": {
                "mean": round(float(np.mean(T_end)), 2),
                "std_dev": round(std_T, 2),
                "P10": round(float(np.percentile(T_end, 10)), 2),
                "P50_median": round(float(np.percentile(T_end, 50)), 2),
                "P80": round(float(np.percentile(T_end, 80)), 2),
                "P90": round(float(np.percentile(T_end, 90)), 2),
                "P95": round(float(np.percentile(T_end, 95)), 2),
                "probability_on_time": round(float(np.mean(T_end <= project.contractual_deadline_days)), 4),
            },
            "cost_distribution_kes": {
                "mean": round(float(np.mean(total_costs)), 2),
                "std_dev": round(float(np.std(total_costs)), 2),
                "P10": round(float(np.percentile(total_costs, 10)), 2),
                "P50_median": round(float(np.percentile(total_costs, 50)), 2),
                "P80": round(float(np.percentile(total_costs, 80)), 2),
                "P90": round(float(np.percentile(total_costs, 90)), 2),
                "P95": round(float(np.percentile(total_costs, 95)), 2),
                "probability_within_budget": round(float(np.mean(total_costs <= project.budget_ceiling_kes)), 4),
            },
            "activity_sensitivity": sensitivity_metrics,
        }

    @staticmethod
    def evaluate_bayesian_risk_network(
        priors: RiskFactorProbabilities,
        observed_evidence: Optional[Dict[str, bool]] = None,
    ) -> Dict[str, float]:
        """
        Exact inference over a 2-layer Construction Bayesian Network (DAG) by enumerating
        the joint distribution over root causes:
          Root Nodes: Weather (W), SupplierDelay (S), EquipmentBreakdown (E), DesignChange (D), PriceSpike (P)
          Intermediate Nodes:
            - ProductivityLoss (L): P(L=1 | W, E) = 1 - (1 - 0.05)*(1 - 0.55*W)*(1 - 0.65*E)  (Noisy-OR)
            - ReworkOccurrence (R): P(R=1 | D, L) = 1 - (1 - 0.04)*(1 - 0.70*D)*(1 - 0.35*L)
          Leaf Consequence Nodes:
            - ScheduleOverrun (SO): P(SO=1 | S, L, R) = 1 - (1 - 0.05)*(1 - 0.75*S)*(1 - 0.60*L)*(1 - 0.50*R)
            - CostOverrun (CO):     P(CO=1 | P, R, SO) = 1 - (1 - 0.05)*(1 - 0.70*P)*(1 - 0.65*R)*(1 - 0.55*SO)
        Supports conditioning on `observed_evidence` (e.g., {"Weather": True, "SupplierDelay": True})
        via Bayes' Rule P(Target | Evidence) = P(Target, Evidence) / P(Evidence).
        """
        evidence = observed_evidence or {}
        root_probs = {
            "Weather": priors.p_weather_disruption,
            "SupplierDelay": priors.p_supplier_delay,
            "EquipmentBreakdown": priors.p_equipment_breakdown,
            "DesignChange": priors.p_design_change,
            "PriceSpike": priors.p_price_spike,
        }

        total_prob_evidence = 0.0
        post_prod_loss = 0.0
        post_rework = 0.0
        post_sched_overrun = 0.0
        post_cost_overrun = 0.0

        for w in (0, 1):
            for s in (0, 1):
                for e in (0, 1):
                    for d in (0, 1):
                        for p in (0, 1):
                            for l_node in (0, 1):
                                for r_node in (0, 1):
                                    for so in (0, 1):
                                        for co in (0, 1):
                                            state = {
                                                "Weather": bool(w),
                                                "SupplierDelay": bool(s),
                                                "EquipmentBreakdown": bool(e),
                                                "DesignChange": bool(d),
                                                "PriceSpike": bool(p),
                                                "ProductivityLoss": bool(l_node),
                                                "Rework": bool(r_node),
                                                "ScheduleOverrun": bool(so),
                                                "CostOverrun": bool(co),
                                            }
                                            if any(state[k] != v for k, v in evidence.items() if k in state):
                                                continue

                                            pw = root_probs["Weather"] if w else (1.0 - root_probs["Weather"])
                                            ps = root_probs["SupplierDelay"] if s else (1.0 - root_probs["SupplierDelay"])
                                            pe = root_probs["EquipmentBreakdown"] if e else (1.0 - root_probs["EquipmentBreakdown"])
                                            pd = root_probs["DesignChange"] if d else (1.0 - root_probs["DesignChange"])
                                            pp = root_probs["PriceSpike"] if p else (1.0 - root_probs["PriceSpike"])

                                            p_l1 = 1.0 - (1.0 - 0.05) * (1.0 - 0.55 * w) * (1.0 - 0.65 * e)
                                            pl = p_l1 if l_node else (1.0 - p_l1)

                                            p_r1 = 1.0 - (1.0 - 0.04) * (1.0 - 0.70 * d) * (1.0 - 0.35 * l_node)
                                            pr = p_r1 if r_node else (1.0 - p_r1)

                                            p_so1 = 1.0 - (1.0 - 0.05) * (1.0 - 0.75 * s) * (1.0 - 0.60 * l_node) * (1.0 - 0.50 * r_node)
                                            pso = p_so1 if so else (1.0 - p_so1)

                                            p_co1 = 1.0 - (1.0 - 0.05) * (1.0 - 0.70 * p) * (1.0 - 0.65 * r_node) * (1.0 - 0.55 * so)
                                            pco = p_co1 if co else (1.0 - p_co1)

                                            joint_p = pw * ps * pe * pd * pp * pl * pr * pso * pco
                                            total_prob_evidence += joint_p
                                            if l_node:
                                                post_prod_loss += joint_p
                                            if r_node:
                                                post_rework += joint_p
                                            if so:
                                                post_sched_overrun += joint_p
                                            if co:
                                                post_cost_overrun += joint_p

        if total_prob_evidence <= 1e-15:
            raise ValueError("Observed evidence has zero probability in the Bayesian Network.")

        return {
            "posterior_productivity_loss_prob": round(post_prod_loss / total_prob_evidence, 4),
            "posterior_rework_prob": round(post_rework / total_prob_evidence, 4),
            "posterior_schedule_overrun_prob": round(post_sched_overrun / total_prob_evidence, 4),
            "posterior_cost_overrun_prob": round(post_cost_overrun / total_prob_evidence, 4),
            "evidence_marginal_likelihood": round(total_prob_evidence, 6),
        }

    @staticmethod
    def compute_critical_chain_buffers(project: ConstructionProject) -> Dict[str, object]:
        """
        Implements Goldratt's Critical-Chain Project Management (CCPM) buffer sizing
        using the Root Square Error Method (RSEM):
          sigma_i = (Pessimistic_i - MostLikely_i) / 2
          Project Buffer PB = 2 * sqrt( sum_{i in CriticalChain} sigma_i^2 )
        """
        cpm = CriticalPathEngine.compute_cpm(project)
        crit_paths = cpm["critical_paths"]
        chain = crit_paths[0] if crit_paths else list(project.activities.keys())

        sum_sq_chain = 0.0
        aggressive_chain_duration = 0.0
        for aid in chain:
            act = project.activities[aid]
            m = act.most_likely_days if act.most_likely_days is not None else float(act.duration_days)
            p = act.pessimistic_days if act.pessimistic_days is not None else act.duration_days * 1.35
            uncertainty = max(0.0, (p - m) / 2.0)
            sum_sq_chain += uncertainty ** 2
            aggressive_chain_duration += m

        project_buffer_days = 2.0 * math.sqrt(sum_sq_chain)

        # Feeding buffer for non-critical activities merging into critical chain
        non_chain = [aid for aid in project.activities if aid not in set(chain)]
        feeding_sum_sq = 0.0
        for aid in non_chain:
            act = project.activities[aid]
            m = act.most_likely_days if act.most_likely_days is not None else float(act.duration_days)
            p = act.pessimistic_days if act.pessimistic_days is not None else act.duration_days * 1.35
            feeding_sum_sq += max(0.0, (p - m) / 2.0) ** 2
        feeding_buffer_days = 2.0 * math.sqrt(feeding_sum_sq) if non_chain else 0.0

        return {
            "critical_chain_activities": chain,
            "aggressive_median_chain_days": round(aggressive_chain_duration, 2),
            "rsem_project_buffer_days": round(project_buffer_days, 2),
            "rsem_feeding_buffer_days": round(feeding_buffer_days, 2),
            "buffered_completion_target_days": round(aggressive_chain_duration + project_buffer_days, 2),
        }

    @staticmethod
    def compute_gamma_robust_cost_bound(
        nominal_costs_kes: List[float],
        max_deviations_kes: List[float],
        protection_budget_gamma: float,
    ) -> Dict[str, float]:
        """
        Implements the Bertsimas & Sim (2004) Gamma-Robust Optimization bound:
          Cost_robust(Gamma) = sum_i c_i^{nom} + max_{S subset V, |S| <= Gamma} sum_{i in S} delta_c_i
        Guarantees feasibility if up to Gamma cost items simultaneously hit their worst-case shock.
        """
        if len(nominal_costs_kes) != len(max_deviations_kes):
            raise ValueError("Nominal costs and max deviations must have identical length.")
        if protection_budget_gamma < 0:
            raise ValueError("Gamma protection parameter must be non-negative.")

        nominal_total = sum(nominal_costs_kes)
        sorted_devs = sorted((max(0.0, d) for d in max_deviations_kes), reverse=True)
        full_count = int(math.floor(protection_budget_gamma))
        frac = protection_budget_gamma - full_count

        protection_surcharge = sum(sorted_devs[:full_count])
        if full_count < len(sorted_devs) and frac > 0:
            protection_surcharge += frac * sorted_devs[full_count]

        return {
            "nominal_cost_kes": round(nominal_total, 2),
            "protection_gamma": protection_budget_gamma,
            "robust_protection_buffer_kes": round(protection_surcharge, 2),
            "gamma_robust_total_cost_kes": round(nominal_total + protection_surcharge, 2),
        }
