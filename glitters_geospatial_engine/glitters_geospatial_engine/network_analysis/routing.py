"""
Spatial Network & Accessibility Intelligence Engine (`network_analysis/routing.py` v1.1.0)
==========================================================================================

Upgrades in v1.1.0:
1. Enforces metric CRS validation (`KenyaCRSEngine.assert_projected_crs(utm_epsg)`) at
   `SpatialNetworkEngine` initialization.
2. Replaces naive convex-hull isochrone generation with **Network-Corridor Edge Buffering**:
   traces all reachable road segments (and partial edge segments up to remaining travel time
   budget) and unions their buffered corridors (`unary_union`), preventing isochrones from
   falsely bridging across unbridged river gorges or inaccessible private land.
"""

from __future__ import annotations
from dataclasses import dataclass
import heapq
import math
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple
from shapely.geometry import LineString, Point, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from ..crs.kenya_crs import KenyaCRSEngine


KENYA_ROAD_SPEED_PROFILES_KMH: Dict[str, Dict[str, float]] = {
    "CLASS_A_INTERNATIONAL_TRUNK_TARMAC": {"dry_kmh": 80.0, "wet_kmh": 65.0},
    "CLASS_B_NATIONAL_TRUNK_TARMAC": {"dry_kmh": 70.0, "wet_kmh": 55.0},
    "CLASS_C_PRIMARY_COLLECTOR_TARMAC": {"dry_kmh": 50.0, "wet_kmh": 40.0},
    "URBAN_ARTERIAL_TARMAC": {"dry_kmh": 40.0, "wet_kmh": 28.0},
    "GRADED_MURRAM_ALL_WEATHER": {"dry_kmh": 35.0, "wet_kmh": 20.0},
    "UNPAVED_EARTH_FEEDER": {"dry_kmh": 22.0, "wet_kmh": 8.0},
}


@dataclass(frozen=True)
class NetworkEdge:
    u: int
    v: int
    length_m: float
    travel_time_sec: float
    road_class: str = "URBAN_ARTERIAL_TARMAC"
    is_shortcut: bool = False
    contracted_via: Optional[int] = None


@dataclass(frozen=True)
class RouteResult:
    source_node: int
    target_node: int
    reachable: bool
    path_nodes: Tuple[int, ...]
    network_distance_m: float
    straight_line_distance_m: float
    detour_circuity_index: float
    travel_time_sec: float
    travel_time_min: float
    visited_nodes_count: int


class SpatialNetworkEngine:
    """
    Graph-based spatial routing and accessibility analyzer in validated metric UTM coordinates.
    """

    def __init__(self, utm_epsg: int = 32737) -> None:
        KenyaCRSEngine.assert_projected_crs(utm_epsg)
        self.utm_epsg = utm_epsg
        self.node_coords: Dict[int, Tuple[float, float]] = {}
        self.adj_forward: Dict[int, List[NetworkEdge]] = {}
        self.adj_backward: Dict[int, List[NetworkEdge]] = {}
        self._ch_rank: Dict[int, int] = {}
        self._ch_up_adj: Dict[int, List[Tuple[int, float]]] = {}
        self._ch_down_adj: Dict[int, List[Tuple[int, float]]] = {}

    def add_node(self, node_id: int, x_m: float, y_m: float) -> None:
        self.node_coords[node_id] = (float(x_m), float(y_m))
        self.adj_forward.setdefault(node_id, [])
        self.adj_backward.setdefault(node_id, [])

    def add_edge(
        self,
        u: int,
        v: int,
        length_m: Optional[float] = None,
        road_class: str = "URBAN_ARTERIAL_TARMAC",
        season: str = "dry",
        bidirectional: bool = True,
    ) -> None:
        if u not in self.node_coords or v not in self.node_coords:
            raise KeyError("Both endpoint nodes must be added before adding an edge.")
        x1, y1 = self.node_coords[u]
        x2, y2 = self.node_coords[v]
        euclid_m = math.hypot(x2 - x1, y2 - y1)
        dist_m = float(length_m) if length_m is not None else euclid_m
        if dist_m + 1e-6 < euclid_m:
            raise ValueError("Edge network length cannot be shorter than Euclidean straight-line distance.")

        profile = KENYA_ROAD_SPEED_PROFILES_KMH.get(
            road_class, KENYA_ROAD_SPEED_PROFILES_KMH["URBAN_ARTERIAL_TARMAC"]
        )
        speed_kmh = profile["wet_kmh"] if season.lower() == "wet" else profile["dry_kmh"]
        speed_ms = max(0.5, speed_kmh / 3.6)
        tt_sec = dist_m / speed_ms

        edge_uv = NetworkEdge(u=u, v=v, length_m=dist_m, travel_time_sec=tt_sec, road_class=road_class)
        self.adj_forward[u].append(edge_uv)
        self.adj_backward[v].append(edge_uv)

        if bidirectional:
            edge_vu = NetworkEdge(u=v, v=u, length_m=dist_m, travel_time_sec=tt_sec, road_class=road_class)
            self.adj_forward[v].append(edge_vu)
            self.adj_backward[u].append(edge_vu)

    def snap_point_to_nearest_node(self, x_m: float, y_m: float) -> Tuple[int, float]:
        best_node = -1
        best_dist = float("inf")
        for nid, (nx, ny) in self.node_coords.items():
            d = math.hypot(x_m - nx, y_m - ny)
            if d < best_dist:
                best_dist = d
                best_node = nid
        return best_node, best_dist

    def shortest_path_dijkstra(
        self, source: int, target: int, weight: str = "travel_time_sec"
    ) -> RouteResult:
        dist: Dict[int, float] = {source: 0.0}
        prev: Dict[int, Tuple[int, NetworkEdge]] = {}
        pq: List[Tuple[float, int]] = [(0.0, source)]
        visited_count = 0

        while pq:
            d_u, u = heapq.heappop(pq)
            if d_u > dist.get(u, float("inf")):
                continue
            visited_count += 1
            if u == target:
                break
            for edge in self.adj_forward.get(u, []):
                w = edge.travel_time_sec if weight == "travel_time_sec" else edge.length_m
                nd = d_u + w
                if nd < dist.get(edge.v, float("inf")):
                    dist[edge.v] = nd
                    prev[edge.v] = (u, edge)
                    heapq.heappush(pq, (nd, edge.v))

        return self._reconstruct_route(source, target, dist, prev, visited_count)

    def shortest_path_astar(
        self, source: int, target: int, weight: str = "travel_time_sec"
    ) -> RouteResult:
        tx, ty = self.node_coords[target]
        v_max_ms = 80.0 / 3.6

        def heuristic(u: int) -> float:
            ux, uy = self.node_coords[u]
            euclid = math.hypot(tx - ux, ty - uy)
            return euclid / v_max_ms if weight == "travel_time_sec" else euclid

        g_score: Dict[int, float] = {source: 0.0}
        prev: Dict[int, Tuple[int, NetworkEdge]] = {}
        pq: List[Tuple[float, float, int]] = [(heuristic(source), 0.0, source)]
        visited_count = 0

        while pq:
            f_u, g_u, u = heapq.heappop(pq)
            if g_u > g_score.get(u, float("inf")):
                continue
            visited_count += 1
            if u == target:
                break
            for edge in self.adj_forward.get(u, []):
                w = edge.travel_time_sec if weight == "travel_time_sec" else edge.length_m
                tentative_g = g_u + w
                if tentative_g < g_score.get(edge.v, float("inf")):
                    g_score[edge.v] = tentative_g
                    prev[edge.v] = (u, edge)
                    heapq.heappush(pq, (tentative_g + heuristic(edge.v), tentative_g, edge.v))

        return self._reconstruct_route(source, target, g_score, prev, visited_count)

    def _reconstruct_route(
        self,
        source: int,
        target: int,
        dist_map: Dict[int, float],
        prev: Dict[int, Tuple[int, NetworkEdge]],
        visited_count: int,
    ) -> RouteResult:
        sx, sy = self.node_coords[source]
        tx, ty = self.node_coords[target]
        sl_dist = math.hypot(tx - sx, ty - sy)

        if target not in dist_map:
            return RouteResult(
                source_node=source,
                target_node=target,
                reachable=False,
                path_nodes=(),
                network_distance_m=float("inf"),
                straight_line_distance_m=sl_dist,
                detour_circuity_index=float("inf"),
                travel_time_sec=float("inf"),
                travel_time_min=float("inf"),
                visited_nodes_count=visited_count,
            )

        path: List[int] = [target]
        total_len = 0.0
        total_time = 0.0
        curr = target
        while curr != source:
            pred, edge = prev[curr]
            total_len += edge.length_m
            total_time += edge.travel_time_sec
            path.append(pred)
            curr = pred
        path.reverse()

        detour = total_len / max(sl_dist, 1e-6) if sl_dist > 0 else 1.0
        return RouteResult(
            source_node=source,
            target_node=target,
            reachable=True,
            path_nodes=tuple(path),
            network_distance_m=total_len,
            straight_line_distance_m=sl_dist,
            detour_circuity_index=max(1.0, detour),
            travel_time_sec=total_time,
            travel_time_min=total_time / 60.0,
            visited_nodes_count=visited_count,
        )

    def build_contraction_hierarchy(self, weight: str = "travel_time_sec") -> Dict[str, int]:
        nodes = list(self.node_coords.keys())

        def node_importance(nid: int) -> Tuple[int, int]:
            edges = self.adj_forward.get(nid, [])
            max_tier = 0
            for e in edges:
                if "CLASS_A" in e.road_class:
                    max_tier = max(max_tier, 4)
                elif "CLASS_B" in e.road_class:
                    max_tier = max(max_tier, 3)
                elif "ARTERIAL" in e.road_class:
                    max_tier = max(max_tier, 2)
                else:
                    max_tier = max(max_tier, 1)
            return (max_tier, (nid % 2) + ((nid // 20) % 2))

        nodes.sort(key=node_importance)
        self._ch_rank = {nid: rank for rank, nid in enumerate(nodes)}

        working_out: Dict[int, Dict[int, float]] = {n: {} for n in nodes}
        working_in: Dict[int, Dict[int, float]] = {n: {} for n in nodes}
        for u, edges in self.adj_forward.items():
            for e in edges:
                w = e.travel_time_sec if weight == "travel_time_sec" else e.length_m
                if e.v not in working_out[u] or w < working_out[u][e.v]:
                    working_out[u][e.v] = w
                    working_in[e.v][u] = w

        contracted: Set[int] = set()
        shortcuts_added = 0

        def has_witness_path(
            u_src: int, v_skip: int, targets: Dict[int, float], max_cost: float, max_settled: int = 25
        ) -> Set[int]:
            dist_w: Dict[int, float] = {u_src: 0.0}
            pq_w: List[Tuple[float, int]] = [(0.0, u_src)]
            witnessed: Set[int] = set()
            settled = 0
            while pq_w and settled < max_settled and len(witnessed) < len(targets):
                d_curr, curr = heapq.heappop(pq_w)
                if d_curr > max_cost:
                    break
                if d_curr > dist_w.get(curr, float("inf")):
                    continue
                settled += 1
                if curr in targets and d_curr <= targets[curr] + 1e-7:
                    witnessed.add(curr)
                for nxt, w_edge in working_out[curr].items():
                    if nxt == v_skip or nxt in contracted:
                        continue
                    nd = d_curr + w_edge
                    if nd <= max_cost + 1e-7 and nd < dist_w.get(nxt, float("inf")):
                        dist_w[nxt] = nd
                        heapq.heappush(pq_w, (nd, nxt))
            return witnessed

        for v in nodes:
            in_neighbors = [u for u in working_in[v] if u not in contracted]
            out_neighbors = [w for w in working_out[v] if w not in contracted]
            for u in in_neighbors:
                du_v = working_in[v][u]
                targets = {w: du_v + working_out[v][w] for w in out_neighbors if w != u}
                if not targets:
                    continue
                max_c = max(targets.values())
                witnessed = has_witness_path(u, v, targets, max_c)
                for w, sc_cost in targets.items():
                    if w not in witnessed:
                        if sc_cost < working_out[u].get(w, float("inf")):
                            working_out[u][w] = sc_cost
                            working_in[w][u] = sc_cost
                            shortcuts_added += 1
            contracted.add(v)

        self._ch_up_adj = {n: [] for n in nodes}
        self._ch_down_adj = {n: [] for n in nodes}
        for u in nodes:
            for v, cost in working_out[u].items():
                if self._ch_rank[u] < self._ch_rank[v]:
                    self._ch_up_adj[u].append((v, cost))
            for v, cost in working_in[u].items():
                if self._ch_rank[u] < self._ch_rank[v]:
                    self._ch_down_adj[u].append((v, cost))

        return {"total_nodes": len(nodes), "shortcuts_added": shortcuts_added}

    def query_contraction_hierarchy(self, source: int, target: int) -> float:
        if not self._ch_rank:
            self.build_contraction_hierarchy()

        dist_f: Dict[int, float] = {source: 0.0}
        dist_b: Dict[int, float] = {target: 0.0}
        pq_f: List[Tuple[float, int]] = [(0.0, source)]
        pq_b: List[Tuple[float, int]] = [(0.0, target)]
        best = float("inf")

        while pq_f or pq_b:
            if pq_f:
                d_u, u = heapq.heappop(pq_f)
                if d_u <= dist_f.get(u, float("inf")) and d_u < best:
                    if u in dist_b and d_u + dist_b[u] < best:
                        best = d_u + dist_b[u]
                    for v, w in self._ch_up_adj.get(u, []):
                        nd = d_u + w
                        if nd < best and nd < dist_f.get(v, float("inf")):
                            dist_f[v] = nd
                            heapq.heappush(pq_f, (nd, v))
            if pq_b:
                d_u, u = heapq.heappop(pq_b)
                if d_u <= dist_b.get(u, float("inf")) and d_u < best:
                    if u in dist_f and d_u + dist_f[u] < best:
                        best = d_u + dist_f[u]
                    for v, w in self._ch_down_adj.get(u, []):
                        nd = d_u + w
                        if nd < best and nd < dist_b.get(v, float("inf")):
                            dist_b[v] = nd
                            heapq.heappush(pq_b, (nd, v))

        return best

    def multi_source_nearest_facility(
        self, facility_nodes: Sequence[int], weight: str = "travel_time_sec"
    ) -> Dict[int, Dict[str, Any]]:
        dist: Dict[int, float] = {}
        nearest_fac: Dict[int, int] = {}
        pq: List[Tuple[float, int, int]] = []

        for f_node in facility_nodes:
            dist[f_node] = 0.0
            nearest_fac[f_node] = f_node
            heapq.heappush(pq, (0.0, f_node, f_node))

        while pq:
            d_u, u, root_fac = heapq.heappop(pq)
            if d_u > dist.get(u, float("inf")):
                continue
            for edge in self.adj_backward.get(u, []):
                w = edge.travel_time_sec if weight == "travel_time_sec" else edge.length_m
                nd = d_u + w
                if nd < dist.get(edge.u, float("inf")):
                    dist[edge.u] = nd
                    nearest_fac[edge.u] = root_fac
                    heapq.heappush(pq, (nd, edge.u, root_fac))

        results: Dict[int, Dict[str, Any]] = {}
        for nid, (nx, ny) in self.node_coords.items():
            if nid in dist:
                fac_id = nearest_fac[nid]
                fx, fy = self.node_coords[fac_id]
                results[nid] = {
                    "nearest_facility_node": fac_id,
                    "network_cost": dist[nid],
                    "straight_line_distance_m": math.hypot(fx - nx, fy - ny),
                }
        return results

    def compute_isochrone_polygon(
        self, source_node: int, max_travel_time_sec: float = 900.0, buffer_m: float = 80.0
    ) -> BaseGeometry:
        """
        Generate a road-corridor service-area isochrone geometry by buffering the exact
        reachable road segments (and linearly interpolated partial segments where an edge
        crosses the `max_travel_time_sec` boundary), rather than taking a naive convex hull
        that bridges across unbridged rivers or physical barriers.
        """
        dist: Dict[int, float] = {source_node: 0.0}
        pq: List[Tuple[float, int]] = [(0.0, source_node)]

        while pq:
            d_u, u = heapq.heappop(pq)
            if d_u > dist.get(u, float("inf")):
                continue
            if d_u <= max_travel_time_sec:
                for edge in self.adj_forward.get(u, []):
                    nd = d_u + edge.travel_time_sec
                    if nd < dist.get(edge.v, float("inf")):
                        dist[edge.v] = nd
                        if nd <= max_travel_time_sec:
                            heapq.heappush(pq, (nd, edge.v))

        corridor_segments: List[BaseGeometry] = []
        for u, d_u in dist.items():
            if d_u > max_travel_time_sec:
                continue
            ux, uy = self.node_coords[u]
            corridor_segments.append(Point(ux, uy))
            for edge in self.adj_forward.get(u, []):
                vx, vy = self.node_coords[edge.v]
                d_v = dist.get(edge.v, float("inf"))
                if d_v <= max_travel_time_sec and d_u + edge.travel_time_sec <= max_travel_time_sec * 1.05:
                    corridor_segments.append(LineString([(ux, uy), (vx, vy)]))
                elif edge.travel_time_sec > 0:
                    # Partial traversal along edge (u -> v) up to remaining budget
                    rem_frac = min(1.0, max(0.0, (max_travel_time_sec - d_u) / edge.travel_time_sec))
                    if rem_frac > 0.01:
                        px = ux + rem_frac * (vx - ux)
                        py = uy + rem_frac * (vy - uy)
                        corridor_segments.append(LineString([(ux, uy), (px, py)]))

        if not corridor_segments:
            sx, sy = self.node_coords[source_node]
            return Point(sx, sy).buffer(buffer_m)
        merged_network = unary_union(corridor_segments)
        return merged_network.buffer(buffer_m)
