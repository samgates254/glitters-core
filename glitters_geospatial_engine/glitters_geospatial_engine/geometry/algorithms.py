"""
Computational Geometry Engine (`geometry/algorithms.py` v1.1.0)
===============================================================

Upgrades in v1.1.0:
1. System-wide metric CRS validation (`assert_projected_crs`) whenever `crs_epsg` is provided.
2. Canonical ring orientation (counter-clockwise) and canonical starting-vertex alignment
   before computing Discrete Fréchet distance so `compare_boundaries` is invariant to ring
   winding direction and starting vertex shift.
3. Index-driven `nearest_neighbours` using `STRtree.query_nearest` and expanding bounding windows
   instead of brute-force linear scans.
4. Sparse CSR matrix `local_morans_i` using `cKDTree.sparse_distance_matrix`, eliminating the
   dense O(N^2) memory bottleneck.
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union
import math
import numpy as np
from scipy.sparse import csr_matrix
from scipy.spatial import cKDTree
from scipy.spatial.distance import directed_hausdorff
import shapely
from shapely.geometry import (
    GeometryCollection,
    LineString,
    MultiPolygon,
    Point,
    Polygon,
)
from shapely.geometry.base import BaseGeometry
from shapely.ops import orient as shapely_orient, split as shapely_split, unary_union
from shapely.strtree import STRtree
from shapely.validation import explain_validity, make_valid

from ..crs.kenya_crs import KenyaCRSEngine


@dataclass(frozen=True)
class GeometryValidityReport:
    is_valid: bool
    is_simple: bool
    validity_reason: str
    has_self_intersection: bool
    has_bowtie_or_ring_touch: bool
    minimum_clearance_m: float
    polsby_popper_compactness: float
    vertex_count: int
    repaired_geometry: Optional[BaseGeometry] = None


@dataclass(frozen=True)
class BoundaryComparisonMetrics:
    intersection_over_union: float
    dice_coefficient: float
    symmetric_difference_area_m2: float
    hausdorff_distance_m: float
    directed_hausdorff_ab_m: float
    directed_hausdorff_ba_m: float
    discrete_frechet_distance_m: float
    centroid_displacement_m: float
    area_relative_change: float
    perimeter_relative_change: float


class ComputationalGeometryEngine:
    """
    Numerically robust computational geometry processor operating on metric planar
    coordinates (validated via `KenyaCRSEngine.assert_projected_crs`).
    """

    def __init__(
        self,
        grid_size_m: float = 0.001,
        boundary_epsilon_m: float = 1e-6,
        default_crs_epsg: int = 32737,
    ) -> None:
        KenyaCRSEngine.assert_projected_crs(default_crs_epsg)
        self.grid_size_m = grid_size_m
        self.boundary_epsilon_m = boundary_epsilon_m
        self.default_crs_epsg = default_crs_epsg

    def _validate_crs_if_given(self, crs_epsg: Optional[Union[int, str]]) -> None:
        if crs_epsg is not None:
            KenyaCRSEngine.assert_projected_crs(crs_epsg)

    # -------------------------------------------------------------------------
    # 1. Point-in-Polygon (Winding Number + Boundary Epsilon)
    # -------------------------------------------------------------------------
    def point_in_polygon_winding(
        self, point_xy: Tuple[float, float], ring_coords: Sequence[Tuple[float, float]]
    ) -> str:
        px, py = point_xy
        n = len(ring_coords)
        if n < 3:
            return "OUTSIDE"

        winding_number = 0
        eps = self.boundary_epsilon_m

        for i in range(n - 1):
            x1, y1 = ring_coords[i]
            x2, y2 = ring_coords[i + 1]

            dx, dy = x2 - x1, y2 - y1
            seg_len_sq = dx * dx + dy * dy
            if seg_len_sq > 0.0:
                t = max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / seg_len_sq))
                proj_x = x1 + t * dx
                proj_y = y1 + t * dy
                if math.hypot(px - proj_x, py - proj_y) <= eps:
                    return "BOUNDARY"
            elif math.hypot(px - x1, py - y1) <= eps:
                return "BOUNDARY"

            is_left = (x2 - x1) * (py - y1) - (px - x1) * (y2 - y1)
            if y1 <= py:
                if y2 > py and is_left > 0.0:
                    winding_number += 1
            else:
                if y2 <= py and is_left < 0.0:
                    winding_number -= 1

        return "INSIDE" if winding_number != 0 else "OUTSIDE"

    def point_in_polygon(
        self, point: Point, polygon: Polygon, crs_epsg: Optional[int] = None
    ) -> Dict[str, Any]:
        self._validate_crs_if_given(crs_epsg)
        ext_status = self.point_in_polygon_winding(
            (point.x, point.y), list(polygon.exterior.coords)
        )
        if ext_status == "BOUNDARY":
            return {"classification": "BOUNDARY", "contains": False, "covers": True}
        if ext_status == "OUTSIDE":
            return {"classification": "OUTSIDE", "contains": False, "covers": False}

        for interior in polygon.interiors:
            hole_status = self.point_in_polygon_winding(
                (point.x, point.y), list(interior.coords)
            )
            if hole_status == "BOUNDARY":
                return {"classification": "BOUNDARY", "contains": False, "covers": True}
            if hole_status == "INSIDE":
                return {"classification": "OUTSIDE", "contains": False, "covers": False}

        return {"classification": "INSIDE", "contains": True, "covers": True}

    # -------------------------------------------------------------------------
    # 2. Snap-Rounded Boolean Overlay Operations
    # -------------------------------------------------------------------------
    def snap_to_precision_grid(self, geom: BaseGeometry) -> BaseGeometry:
        if self.grid_size_m > 0:
            return shapely.set_precision(geom, grid_size=self.grid_size_m)
        return geom

    def polygon_intersection(
        self, poly_a: BaseGeometry, poly_b: BaseGeometry, crs_epsg: Optional[int] = None
    ) -> BaseGeometry:
        self._validate_crs_if_given(crs_epsg)
        a = self.snap_to_precision_grid(make_valid(poly_a) if not poly_a.is_valid else poly_a)
        b = self.snap_to_precision_grid(make_valid(poly_b) if not poly_b.is_valid else poly_b)
        return shapely.intersection(a, b, grid_size=self.grid_size_m if self.grid_size_m > 0 else None)

    def polygon_union(
        self, poly_a: BaseGeometry, poly_b: BaseGeometry, crs_epsg: Optional[int] = None
    ) -> BaseGeometry:
        self._validate_crs_if_given(crs_epsg)
        a = self.snap_to_precision_grid(make_valid(poly_a) if not poly_a.is_valid else poly_a)
        b = self.snap_to_precision_grid(make_valid(poly_b) if not poly_b.is_valid else poly_b)
        return shapely.union(a, b, grid_size=self.grid_size_m if self.grid_size_m > 0 else None)

    def polygon_difference(
        self, poly_a: BaseGeometry, poly_b: BaseGeometry, crs_epsg: Optional[int] = None
    ) -> BaseGeometry:
        self._validate_crs_if_given(crs_epsg)
        a = self.snap_to_precision_grid(make_valid(poly_a) if not poly_a.is_valid else poly_a)
        b = self.snap_to_precision_grid(make_valid(poly_b) if not poly_b.is_valid else poly_b)
        return shapely.difference(a, b, grid_size=self.grid_size_m if self.grid_size_m > 0 else None)

    def polygon_containment(
        self,
        container: BaseGeometry,
        candidate: BaseGeometry,
        tolerance_m: float = 0.05,
        crs_epsg: Optional[int] = None,
    ) -> Dict[str, Any]:
        self._validate_crs_if_given(crs_epsg)
        strict_contains = bool(container.contains(candidate))
        strict_covers = bool(container.covers(candidate))
        buffered_covers = bool(container.buffer(tolerance_m).covers(candidate))
        inter_area = self.polygon_intersection(container, candidate).area
        candidate_area = max(candidate.area, 1e-12)
        coverage_ratio = min(1.0, float(inter_area / candidate_area))
        return {
            "strict_contains": strict_contains,
            "strict_covers": strict_covers,
            "buffered_covers_within_tolerance": buffered_covers,
            "tolerance_m": tolerance_m,
            "candidate_area_inside_ratio": coverage_ratio,
            "de9im_matrix": container.relate(candidate),
        }

    # -------------------------------------------------------------------------
    # 3. Polygon Simplification (Ramer-Douglas-Peucker & Visvalingam-Whyatt)
    # -------------------------------------------------------------------------
    def simplify_rdp(
        self,
        poly: Polygon,
        tolerance_m: float = 0.25,
        preserve_topology: bool = True,
        crs_epsg: Optional[int] = None,
    ) -> Polygon:
        self._validate_crs_if_given(crs_epsg)
        simplified = poly.simplify(tolerance_m, preserve_topology=preserve_topology)
        return simplified if isinstance(simplified, Polygon) else poly

    def simplify_visvalingam_whyatt(
        self,
        poly: Polygon,
        min_triangle_area_m2: float = 0.5,
        crs_epsg: Optional[int] = None,
    ) -> Polygon:
        self._validate_crs_if_given(crs_epsg)
        coords = list(poly.exterior.coords)[:-1]
        if len(coords) <= 3:
            return poly

        def triangle_area(p1: Tuple[float, float], p2: Tuple[float, float], p3: Tuple[float, float]) -> float:
            return 0.5 * abs(p1[0] * (p2[1] - p3[1]) + p2[0] * (p3[1] - p1[1]) + p3[0] * (p1[1] - p2[1]))

        pts = list(coords)
        while len(pts) > 3:
            n = len(pts)
            min_area = float("inf")
            min_idx = -1
            for i in range(n):
                p_prev = pts[(i - 1) % n]
                p_curr = pts[i]
                p_next = pts[(i + 1) % n]
                area = triangle_area(p_prev, p_curr, p_next)
                if area < min_area:
                    min_area = area
                    min_idx = i
            if min_area < min_triangle_area_m2 and min_idx >= 0:
                pts.pop(min_idx)
            else:
                break

        pts.append(pts[0])
        candidate = Polygon(pts)
        if not candidate.is_valid:
            return self.simplify_rdp(poly, tolerance_m=math.sqrt(min_triangle_area_m2), preserve_topology=True)
        return candidate

    # -------------------------------------------------------------------------
    # 4. Geometry Validity Detection & Repair
    # -------------------------------------------------------------------------
    def inspect_and_repair_validity(
        self, geom: BaseGeometry, crs_epsg: Optional[int] = None
    ) -> GeometryValidityReport:
        self._validate_crs_if_given(crs_epsg)
        coords = shapely.get_coordinates(geom)
        if len(coords) == 0 or not np.all(np.isfinite(coords)):
            return GeometryValidityReport(
                is_valid=False,
                is_simple=False,
                validity_reason="Non-finite (NaN/Inf) or empty coordinates",
                has_self_intersection=False,
                has_bowtie_or_ring_touch=False,
                minimum_clearance_m=0.0,
                polsby_popper_compactness=0.0,
                vertex_count=len(coords),
                repaired_geometry=None,
            )

        is_val = bool(geom.is_valid)
        is_simp = bool(geom.is_simple)
        reason = explain_validity(geom)
        has_self_inter = "Self-intersection" in reason or "Ring Self-intersection" in reason
        has_bowtie = "Self-intersection" in reason

        repaired = make_valid(geom) if not is_val else geom
        area = float(repaired.area)
        perim = float(repaired.length)
        pp_compactness = (4.0 * math.pi * area) / (perim * perim) if perim > 1e-9 else 0.0
        min_clearance = float(shapely.minimum_clearance(repaired)) if area > 0 else 0.0
        n_verts = int(shapely.get_num_coordinates(geom))

        return GeometryValidityReport(
            is_valid=is_val,
            is_simple=is_simp,
            validity_reason=reason,
            has_self_intersection=has_self_inter,
            has_bowtie_or_ring_touch=has_bowtie,
            minimum_clearance_m=min_clearance if math.isfinite(min_clearance) else 0.0,
            polsby_popper_compactness=pp_compactness,
            vertex_count=n_verts,
            repaired_geometry=repaired,
        )

    # -------------------------------------------------------------------------
    # 5. Canonicalized Boundary Comparison (Hausdorff, Discrete Fréchet, IoU)
    # -------------------------------------------------------------------------
    @staticmethod
    def canonicalize_ring_coordinates(poly: Polygon, densify_step_m: float = 1.0) -> np.ndarray:
        """
        Canonicalize a polygon's exterior ring so Discrete Fréchet distance is invariant
        to ring winding direction (CW vs CCW) and starting vertex position:
        1. Orient exterior ring counter-clockwise (`shapely.ops.orient(poly, sign=1.0)`).
        2. Rotate the open vertex sequence so index 0 is the lexicographically smallest
           `(x, y)` vertex (south-west anchor), then close the ring.
        3. Densify segments to `densify_step_m`.
        """
        oriented = shapely_orient(poly, sign=1.0)
        raw_ring = np.asarray(oriented.exterior.coords[:-1], dtype=np.float64)
        if len(raw_ring) == 0:
            return np.zeros((0, 2), dtype=np.float64)
        # Lexicographical minimum (min x, then min y) on raw vertices prior to densification
        order_key = np.lexsort((raw_ring[:, 1], raw_ring[:, 0]))
        start_idx = int(order_key[0])
        rolled = np.roll(raw_ring, -start_idx, axis=0)
        closed = np.vstack([rolled, rolled[0]])
        dense_line = shapely.segmentize(LineString(closed), max_segment_length=densify_step_m)
        return np.asarray(dense_line.coords, dtype=np.float64)

    @staticmethod
    def discrete_frechet_distance(curve_a: np.ndarray, curve_b: np.ndarray) -> float:
        n = len(curve_a)
        m = len(curve_b)
        if n == 0 or m == 0:
            return 0.0
        dist_matrix = np.linalg.norm(curve_a[:, None, :] - curve_b[None, :, :], axis=2)
        ca = np.full((n, m), -1.0, dtype=np.float64)
        ca[0, 0] = dist_matrix[0, 0]
        for i in range(1, n):
            ca[i, 0] = max(ca[i - 1, 0], dist_matrix[i, 0])
        for j in range(1, m):
            ca[0, j] = max(ca[0, j - 1], dist_matrix[0, j])
        for i in range(1, n):
            for j in range(1, m):
                ca[i, j] = max(
                    min(ca[i - 1, j], ca[i - 1, j - 1], ca[i, j - 1]),
                    dist_matrix[i, j],
                )
        return float(ca[n - 1, m - 1])

    def compare_boundaries(
        self, poly_a: Polygon, poly_b: Polygon, crs_epsg: Optional[int] = None
    ) -> BoundaryComparisonMetrics:
        """
        Compute boundary comparison metrics between two polygons with canonicalized CCW
        orientation and starting-vertex alignment.
        """
        self._validate_crs_if_given(crs_epsg)
        inter_area = float(self.polygon_intersection(poly_a, poly_b).area)
        union_area = float(self.polygon_union(poly_a, poly_b).area)
        sym_diff_area = max(0.0, union_area - inter_area)
        iou = inter_area / max(union_area, 1e-12)
        dice = (2.0 * inter_area) / max(float(poly_a.area + poly_b.area), 1e-12)

        step_m = max(1.0, min(poly_a.length, poly_b.length) / 80.0)
        pts_a = self.canonicalize_ring_coordinates(poly_a, densify_step_m=step_m)
        pts_b = self.canonicalize_ring_coordinates(poly_b, densify_step_m=step_m)

        # Also align pts_b starting vertex to the closest point to pts_a[0] for closed-loop invariance
        if len(pts_b) > 2:
            b_open = pts_b[:-1]
            nearest_b_start = int(np.argmin(np.linalg.norm(b_open - pts_a[0], axis=1)))
            b_rolled = np.roll(b_open, -nearest_b_start, axis=0)
            pts_b = np.vstack([b_rolled, b_rolled[0]])

        d_ab = float(directed_hausdorff(pts_a, pts_b)[0])
        d_ba = float(directed_hausdorff(pts_b, pts_a)[0])
        hausdorff = max(d_ab, d_ba)
        frechet = self.discrete_frechet_distance(pts_a, pts_b)

        c_a = poly_a.centroid
        c_b = poly_b.centroid
        centroid_disp = math.hypot(c_a.x - c_b.x, c_a.y - c_b.y)

        area_rel = (poly_b.area - poly_a.area) / max(poly_a.area, 1e-12)
        perim_rel = (poly_b.length - poly_a.length) / max(poly_a.length, 1e-12)

        return BoundaryComparisonMetrics(
            intersection_over_union=iou,
            dice_coefficient=dice,
            symmetric_difference_area_m2=sym_diff_area,
            hausdorff_distance_m=hausdorff,
            directed_hausdorff_ab_m=d_ab,
            directed_hausdorff_ba_m=d_ba,
            discrete_frechet_distance_m=frechet,
            centroid_displacement_m=centroid_disp,
            area_relative_change=float(area_rel),
            perimeter_relative_change=float(perim_rel),
        )

    # -------------------------------------------------------------------------
    # 6. Polygon Splitting & Sliver-Filtered Merging
    # -------------------------------------------------------------------------
    def split_polygon_by_line(
        self,
        poly: Polygon,
        cutter: LineString,
        min_part_area_m2: float = 1.0,
        crs_epsg: Optional[int] = None,
    ) -> List[Polygon]:
        self._validate_crs_if_given(crs_epsg)
        collection: GeometryCollection = shapely_split(poly, cutter)
        return [
            g for g in collection.geoms if isinstance(g, Polygon) and g.area >= min_part_area_m2
        ]

    def merge_polygons(
        self,
        polygons: Sequence[Polygon],
        gap_bridge_tolerance_m: float = 0.05,
        crs_epsg: Optional[int] = None,
    ) -> BaseGeometry:
        self._validate_crs_if_given(crs_epsg)
        valid_polys = [make_valid(p) if not p.is_valid else p for p in polygons]
        merged = unary_union(valid_polys)
        if gap_bridge_tolerance_m > 0.0:
            merged = merged.buffer(gap_bridge_tolerance_m, join_style="mitre").buffer(
                -gap_bridge_tolerance_m, join_style="mitre"
            )
        return self.snap_to_precision_grid(merged)

    # -------------------------------------------------------------------------
    # 7. Index-Accelerated Nearest-Neighbour Search & Sparse Spatial Clustering
    # -------------------------------------------------------------------------
    def nearest_neighbours(
        self,
        query_geom: BaseGeometry,
        candidates: Sequence[BaseGeometry],
        k: int = 5,
        crs_epsg: Optional[int] = None,
    ) -> List[Tuple[int, float]]:
        """
        Index-accelerated k-Nearest Neighbour search using `STRtree.query_nearest` for k=1
        and `cKDTree` centroid + bounding-radius STRtree window expansion for k > 1.
        Avoids brute-force linear scanning of all N candidates.
        """
        self._validate_crs_if_given(crs_epsg)
        n = len(candidates)
        if n == 0 or k <= 0:
            return []
        k_eff = min(k, n)
        tree = STRtree(candidates)

        if k_eff == 1:
            idxs, dists = tree.query_nearest(query_geom, return_distance=True, all_matches=False)
            idx_val = int(np.asarray(idxs).ravel()[0])
            dist_val = float(np.asarray(dists).ravel()[0])
            return [(idx_val, dist_val)]

        # Use cKDTree on candidate centroids to establish an exact upper bound on the k-th distance
        centroids = np.array([[g.centroid.x, g.centroid.y] for g in candidates], dtype=np.float64)
        kdtree = cKDTree(centroids)
        q_pt = np.array([query_geom.centroid.x, query_geom.centroid.y], dtype=np.float64)
        probe_k = min(n, max(k_eff * 4, 24))
        _, kd_idxs = kdtree.query(q_pt, k=probe_k)
        kd_idxs_arr = np.atleast_1d(kd_idxs)

        # Exact distance to the k_eff-th centroid candidate gives a valid search radius upper bound
        exact_probe = sorted(
            (int(i), float(query_geom.distance(candidates[int(i)]))) for i in kd_idxs_arr
        )
        exact_probe.sort(key=lambda x: x[1])
        r_bound = exact_probe[k_eff - 1][1]

        # Query STRtree within r_bound of query_geom to catch any large polygon whose edge is closer than its centroid
        window_idxs = tree.query(query_geom.buffer(r_bound + 1e-6), predicate="intersects")
        combined_idxs = set(int(i) for i in kd_idxs_arr) | set(int(i) for i in window_idxs)
        refined = [(idx, float(query_geom.distance(candidates[idx]))) for idx in combined_idxs]
        refined.sort(key=lambda item: (item[1], item[0]))
        return refined[:k_eff]

    def spatial_dbscan(
        self,
        points_xy: np.ndarray,
        eps_m: float = 100.0,
        min_samples: int = 4,
        crs_epsg: Optional[int] = None,
    ) -> Dict[str, Any]:
        self._validate_crs_if_given(crs_epsg)
        n = len(points_xy)
        if n == 0:
            return {"labels": np.array([], dtype=int), "n_clusters": 0, "noise_count": 0}

        kdtree = cKDTree(points_xy)
        neighbors_list = kdtree.query_ball_point(points_xy, r=eps_m)
        labels = np.full(n, -1, dtype=int)
        visited = np.zeros(n, dtype=bool)
        cluster_id = 0

        for i in range(n):
            if visited[i]:
                continue
            visited[i] = True
            neighbors = neighbors_list[i]
            if len(neighbors) < min_samples:
                labels[i] = -1
            else:
                labels[i] = cluster_id
                seed_set = set(nb for nb in neighbors if nb != i)
                seed_queue = list(seed_set)
                idx = 0
                while idx < len(seed_queue):
                    q = seed_queue[idx]
                    idx += 1
                    if not visited[q]:
                        visited[q] = True
                        q_neighbors = neighbors_list[q]
                        if len(q_neighbors) >= min_samples:
                            for q_nb in q_neighbors:
                                if q_nb not in seed_set and q_nb != i:
                                    seed_set.add(q_nb)
                                    seed_queue.append(q_nb)
                    if labels[q] == -1:
                        labels[q] = cluster_id
                cluster_id += 1

        return {
            "labels": labels,
            "n_clusters": int(cluster_id),
            "noise_count": int(np.sum(labels == -1)),
        }

    def local_morans_i(
        self,
        coords_xy: np.ndarray,
        values: np.ndarray,
        bandwidth_m: float = 500.0,
        crs_epsg: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Compute Anselin's Local Indicators of Spatial Association (Local Moran's I_i)
        using a sparse CSR spatial weight matrix built via `cKDTree.sparse_distance_matrix`.
        Memory complexity is O(N * k_avg) rather than dense O(N^2).
        """
        self._validate_crs_if_given(crs_epsg)
        n = len(values)
        z = values.astype(np.float64) - np.mean(values)
        m2 = float(np.sum(z ** 2) / max(n, 1))
        if m2 < 1e-12 or n == 0:
            return {"local_i": np.zeros(n), "z_lag": np.zeros(n), "sparse_nnz": 0}

        kdtree = cKDTree(coords_xy)
        coo = kdtree.sparse_distance_matrix(kdtree, max_distance=bandwidth_m, output_type="coo_matrix")
        # Exclude self-diagonal (row == col or dist == 0)
        off_diag = coo.row != coo.col
        rows = coo.row[off_diag]
        cols = coo.col[off_diag]
        dists = coo.data[off_diag]
        weights = 1.0 / (1.0 + dists)

        w_csr = csr_matrix((weights, (rows, cols)), shape=(n, n), dtype=np.float64)
        row_sums = np.asarray(w_csr.sum(axis=1)).ravel()
        safe_row_sums = np.where(row_sums > 0.0, row_sums, 1.0)
        inv_row_sums = np.where(row_sums > 0.0, 1.0 / safe_row_sums, 0.0)
        # Row-standardize sparse weights via diagonal scaling
        w_std = w_csr.multiply(inv_row_sums[:, None])

        z_lag = np.asarray(w_std.dot(z)).ravel()
        local_i = (z / m2) * z_lag
        return {
            "local_i": local_i,
            "z_lag": z_lag,
            "sparse_nnz": int(w_csr.nnz),
        }
