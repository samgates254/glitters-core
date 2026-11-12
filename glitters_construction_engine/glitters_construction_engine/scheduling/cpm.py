"""
Generalized Critical Path Method (CPM) Engine.
"""
from __future__ import annotations
from collections import deque
from dataclasses import dataclass
import datetime
from typing import Dict, List, Optional, Set
from ..project_model import ConstructionProject, DependencyType, PrecedenceDependency


class CyclicDependencyError(ValueError):
    """Raised when contradictory or cyclic task dependencies exist in the project network."""
    pass


@dataclass(frozen=True)
class ActivityScheduleMetric:
    activity_id: str
    duration_days: int
    earliest_start: int
    earliest_finish: int
    latest_start: int
    latest_finish: int
    total_float: int
    free_float: int
    is_critical: bool
    calendar_earliest_start_date: Optional[datetime.date] = None
    calendar_earliest_finish_date: Optional[datetime.date] = None


class CriticalPathEngine:
    """
    Mathematically rigorous CPM and PERT engine.
    """

    @staticmethod
    def topological_sort_and_validate(project: ConstructionProject) -> List[str]:
        in_degree: Dict[str, int] = {aid: 0 for aid in project.activities}
        adj: Dict[str, List[str]] = {aid: [] for aid in project.activities}
        for dep in project.dependencies:
            adj[dep.predecessor_id].append(dep.successor_id)
            in_degree[dep.successor_id] += 1

        queue = deque(sorted([aid for aid, deg in in_degree.items() if deg == 0]))
        topo_order: List[str] = []
        while queue:
            u = queue.popleft()
            topo_order.append(u)
            for v in sorted(adj[u]):
                in_degree[v] -= 1
                if in_degree[v] == 0:
                    queue.append(v)

        if len(topo_order) != len(project.activities):
            cycle_nodes = sorted(set(project.activities.keys()) - set(topo_order))
            raise CyclicDependencyError(
                f"Contradictory / cyclic dependency detected involving activities: {cycle_nodes}"
            )
        return topo_order

    @staticmethod
    def _min_start_from_dependency(
        es_i: int, ef_i: int, d_j: int, dep_type: DependencyType, lag: int
    ) -> int:
        if dep_type == DependencyType.FS:
            return ef_i + lag
        if dep_type == DependencyType.SS:
            return es_i + lag
        if dep_type == DependencyType.FF:
            return ef_i + lag - d_j
        if dep_type == DependencyType.SF:
            return es_i + lag - d_j
        raise ValueError(f"Unsupported dependency type: {dep_type}")

    @staticmethod
    def _max_finish_from_dependency(
        ls_j: int, lf_j: int, d_i: int, dep_type: DependencyType, lag: int
    ) -> int:
        if dep_type == DependencyType.FS:
            return ls_j - lag
        if dep_type == DependencyType.SS:
            return ls_j - lag + d_i
        if dep_type == DependencyType.FF:
            return lf_j - lag
        if dep_type == DependencyType.SF:
            return lf_j - lag + d_i
        raise ValueError(f"Unsupported dependency type: {dep_type}")

    @classmethod
    def compute_cpm(cls, project: ConstructionProject, max_critical_paths: int = 50) -> Dict[str, object]:
        if not project.activities:
            raise ValueError("Project contains no activities.")

        topo = cls.topological_sort_and_validate(project)
        preds: Dict[str, List[PrecedenceDependency]] = {aid: [] for aid in project.activities}
        succs: Dict[str, List[PrecedenceDependency]] = {aid: [] for aid in project.activities}
        for dep in project.dependencies:
            preds[dep.successor_id].append(dep)
            succs[dep.predecessor_id].append(dep)

        es: Dict[str, int] = {}
        ef: Dict[str, int] = {}

        for aid in topo:
            act = project.activities[aid]
            d_j = act.duration_days
            earliest = act.earliest_start_window
            for dep in preds[aid]:
                i = dep.predecessor_id
                bound = cls._min_start_from_dependency(es[i], ef[i], d_j, dep.dep_type, dep.lag_days)
                if bound > earliest:
                    earliest = bound
            es[aid] = earliest
            ef[aid] = earliest + d_j
            if act.latest_finish_window is not None and ef[aid] > act.latest_finish_window:
                raise ValueError(
                    f"Infeasible activity window: Activity {aid} earliest finish ({ef[aid]}) "
                    f"exceeds latest finish window ({act.latest_finish_window})."
                )

        project_duration = max(ef.values()) if ef else 0

        ls: Dict[str, int] = {}
        lf: Dict[str, int] = {}
        for aid in reversed(topo):
            act = project.activities[aid]
            d_i = act.duration_days
            latest_f = project_duration
            if act.latest_finish_window is not None:
                latest_f = min(latest_f, act.latest_finish_window)
            for dep in succs[aid]:
                j = dep.successor_id
                bound = cls._max_finish_from_dependency(ls[j], lf[j], d_i, dep.dep_type, dep.lag_days)
                if bound < latest_f:
                    latest_f = bound
            lf[aid] = latest_f
            ls[aid] = latest_f - d_i

        metrics: Dict[str, ActivityScheduleMetric] = {}
        critical_activities: List[str] = []
        for aid in topo:
            d_i = project.activities[aid].duration_days
            tf = ls[aid] - es[aid]
            if tf < 0:
                raise ValueError(
                    f"Infeasible schedule detected: Activity {aid} has negative Total Float ({tf})."
                )
            if not succs[aid]:
                ff = project_duration - ef[aid]
            else:
                ff_candidates: List[int] = []
                for dep in succs[aid]:
                    j = dep.successor_id
                    d_j = project.activities[j].duration_days
                    req_es_j = cls._min_start_from_dependency(es[aid], ef[aid], d_j, dep.dep_type, dep.lag_days)
                    ff_candidates.append(es[j] - req_es_j)
                ff = min(ff_candidates) if ff_candidates else 0
            is_crit = (tf == 0)
            if is_crit:
                critical_activities.append(aid)
            cal_es = project.calendar.working_day_index_to_date(project.project_start_date, es[aid])
            cal_ef = project.calendar.working_day_index_to_date(project.project_start_date, ef[aid])
            metrics[aid] = ActivityScheduleMetric(
                activity_id=aid,
                duration_days=d_i,
                earliest_start=es[aid],
                earliest_finish=ef[aid],
                latest_start=ls[aid],
                latest_finish=lf[aid],
                total_float=tf,
                free_float=max(0, ff),
                is_critical=is_crit,
                calendar_earliest_start_date=cal_es,
                calendar_earliest_finish_date=cal_ef,
            )

        crit_set: Set[str] = set(critical_activities)
        crit_adj: Dict[str, List[str]] = {aid: [] for aid in critical_activities}
        crit_in_deg: Dict[str, int] = {aid: 0 for aid in critical_activities}
        for dep in project.dependencies:
            u, v = dep.predecessor_id, dep.successor_id
            if u in crit_set and v in crit_set:
                d_v = project.activities[v].duration_days
                tight = (es[v] == cls._min_start_from_dependency(es[u], ef[u], d_v, dep.dep_type, dep.lag_days))
                if tight:
                    crit_adj[u].append(v)
                    crit_in_deg[v] += 1

        start_nodes = [u for u in critical_activities if crit_in_deg[u] == 0 and es[u] == 0]
        if not start_nodes:
            start_nodes = [u for u in critical_activities if crit_in_deg[u] == 0]

        critical_paths: List[List[str]] = []
        paths_truncated = False

        def _dfs_paths(curr: str, path: List[str]) -> None:
            nonlocal paths_truncated
            if len(critical_paths) >= max_critical_paths:
                paths_truncated = True
                return
            if not crit_adj[curr]:
                if ef[curr] == project_duration:
                    critical_paths.append(list(path))
                return
            for nxt in crit_adj[curr]:
                if len(critical_paths) >= max_critical_paths:
                    paths_truncated = True
                    return
                path.append(nxt)
                _dfs_paths(nxt, path)
                path.pop()

        for s in start_nodes:
            if len(critical_paths) >= max_critical_paths:
                paths_truncated = True
                break
            _dfs_paths(s, [s])

        completion_date = project.calendar.working_day_index_to_date(project.project_start_date, project_duration)
        return {
            "project_id": project.project_id,
            "project_completion_days": project_duration,
            "project_start_date": project.project_start_date.isoformat(),
            "project_completion_date": completion_date.isoformat(),
            "contractual_deadline_days": project.contractual_deadline_days,
            "meets_contractual_deadline": project_duration <= project.contractual_deadline_days,
            "topological_order": topo,
            "critical_activities": critical_activities,
            "critical_paths": critical_paths,
            "critical_paths_truncated": paths_truncated,
            "metrics": metrics,
        }

    @classmethod
    def compute_pert(cls, project: ConstructionProject, target_deadline_days: float) -> Dict[str, object]:
        """
        Remediates Section 4 PERT Audit Finding:
        Instead of searching only over a truncated list of deterministic critical paths,
        runs exact dynamic programming over the ENTIRE topological DAG using PERT expected durations
        `mu_i = (a_i + 4*m_i + b_i)/6` (breaking ties by larger PERT variance `sigma_i^2`)
        to guarantee finding the true maximum-expected-duration PERT path in O(|V| + |E|) time!
        """
        topo = cls.topological_sort_and_validate(project)
        from scipy import stats
        import math

        preds: Dict[str, List[PrecedenceDependency]] = {aid: [] for aid in project.activities}
        for dep in project.dependencies:
            preds[dep.successor_id].append(dep)

        pert_es: Dict[str, float] = {}
        pert_ef: Dict[str, float] = {}
        parent_ptr: Dict[str, Optional[str]] = {aid: None for aid in topo}
        acc_var: Dict[str, float] = {aid: 0.0 for aid in topo}

        for aid in topo:
            act = project.activities[aid]
            mu_j = act.pert_mean
            var_j = act.pert_variance
            best_es = float(act.earliest_start_window)
            best_parent: Optional[str] = None
            best_parent_var = 0.0

            for dep in preds[aid]:
                i = dep.predecessor_id
                if dep.dep_type == DependencyType.FS:
                    cand = pert_ef[i] + dep.lag_days
                elif dep.dep_type == DependencyType.SS:
                    cand = pert_es[i] + dep.lag_days
                elif dep.dep_type == DependencyType.FF:
                    cand = pert_ef[i] + dep.lag_days - mu_j
                else:
                    cand = pert_es[i] + dep.lag_days - mu_j

                if cand > best_es + 1e-9 or (abs(cand - best_es) <= 1e-9 and acc_var[i] > best_parent_var):
                    best_es = cand
                    best_parent = i
                    best_parent_var = acc_var[i]

            pert_es[aid] = best_es
            pert_ef[aid] = best_es + mu_j
            parent_ptr[aid] = best_parent
            acc_var[aid] = best_parent_var + var_j

        # Reconstruct dominant PERT path from terminal node with maximum pert_ef (tie-break by variance)
        terminal = max(topo, key=lambda a: (pert_ef[a], acc_var[a]))
        dominant_pert_path: List[str] = []
        curr: Optional[str] = terminal
        while curr is not None:
            dominant_pert_path.append(curr)
            curr = parent_ptr[curr]
        dominant_pert_path.reverse()

        expected_duration = pert_ef[terminal]
        variance = acc_var[terminal]
        std_dev = math.sqrt(max(0.0, variance))
        if std_dev > 1e-9:
            z_score = (target_deadline_days - expected_duration) / std_dev
            prob = float(stats.norm.cdf(z_score))
        else:
            z_score = 0.0
            prob = 1.0 if expected_duration <= target_deadline_days else 0.0

        return {
            "critical_path_analyzed": dominant_pert_path,
            "full_dag_dp_exact": True,
            "expected_duration_days": round(expected_duration, 3),
            "variance_days2": round(variance, 4),
            "std_dev_days": round(std_dev, 4),
            "target_deadline_days": target_deadline_days,
            "z_score": round(z_score, 4),
            "probability_of_meeting_deadline": round(prob, 4),
        }
