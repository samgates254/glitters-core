"""
Work Breakdown Structure (WBS) Tree Engine.
Models hierarchical decomposition of construction projects with bottom-up budget, cost,
duration, and Earned Value roll-up across WBS nodes.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class WBSNode:
    wbs_code: str
    name: str
    parent_code: Optional[str] = None
    allocated_budget_kes: float = 0.0
    children: List[str] = field(default_factory=list)
    activity_ids: List[str] = field(default_factory=list)


class WorkBreakdownStructure:
    """
    Tree representation T = (V_WBS, E_WBS) supporting hierarchical validation,
    budget consistency checks, and recursive cost/quantity aggregation.
    """
    def __init__(self, root_code: str = "1", root_name: str = "Total Construction Project", root_budget_kes: float = 0.0) -> None:
        self.nodes: Dict[str, WBSNode] = {
            root_code: WBSNode(wbs_code=root_code, name=root_name, parent_code=None, allocated_budget_kes=root_budget_kes)
        }
        self.root_code = root_code

    def add_node(self, wbs_code: str, name: str, parent_code: str, allocated_budget_kes: float = 0.0) -> WBSNode:
        if wbs_code in self.nodes:
            raise ValueError(f"Duplicate WBS code: {wbs_code}")
        if parent_code not in self.nodes:
            raise KeyError(f"Parent WBS code '{parent_code}' does not exist")
        node = WBSNode(
            wbs_code=wbs_code,
            name=name,
            parent_code=parent_code,
            allocated_budget_kes=allocated_budget_kes,
        )
        self.nodes[wbs_code] = node
        self.nodes[parent_code].children.append(wbs_code)
        return node

    def attach_activity(self, wbs_code: str, activity_id: str) -> None:
        if wbs_code not in self.nodes:
            raise KeyError(f"WBS code '{wbs_code}' not found")
        if activity_id not in self.nodes[wbs_code].activity_ids:
            self.nodes[wbs_code].activity_ids.append(activity_id)

    def rollup_activity_costs(self, activity_costs_kes: Dict[str, float]) -> Dict[str, float]:
        """
        Bottom-up recursive aggregation:
        C(u) = sum_{v in children(u)} C(v) + sum_{a in activities(u)} cost(a)
        """
        memo: Dict[str, float] = {}

        def _dfs(code: str) -> float:
            node = self.nodes[code]
            direct = sum(activity_costs_kes.get(aid, 0.0) for aid in node.activity_ids)
            child_sum = sum(_dfs(c) for c in node.children)
            total = direct + child_sum
            memo[code] = total
            return total

        _dfs(self.root_code)
        return memo

    def check_budget_overruns(self, activity_costs_kes: Dict[str, float]) -> Dict[str, Dict[str, float]]:
        rolled = self.rollup_activity_costs(activity_costs_kes)
        overruns: Dict[str, Dict[str, float]] = {}
        for code, node in self.nodes.items():
            if node.allocated_budget_kes > 0 and rolled[code] > node.allocated_budget_kes + 1e-6:
                overruns[code] = {
                    "allocated_budget_kes": node.allocated_budget_kes,
                    "rolled_up_cost_kes": rolled[code],
                    "variance_kes": rolled[code] - node.allocated_budget_kes,
                }
        return overruns
