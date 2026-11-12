"""
Planar Topology & DE-9IM Analysis Engine (`topology/planar_graph.py`)
=====================================================================

Provides Dimensionally Extended 9-Intersection Model (DE-9IM) classification,
planar coverage validation, sliver gap extraction, and overlap decomposition.
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Dict, List, Sequence, Tuple
import shapely
from shapely.geometry import Polygon, MultiPolygon
from shapely.ops import polygonize, unary_union
from shapely.strtree import STRtree


@dataclass(frozen=True)
class TopologicalAnomaly:
    """Represents a topological error (overlap or interstitial gap) in a parcel fabric."""
    anomaly_type: str  # 'OVERLAP', 'SLIVER_OVERLAP', 'INTERSTITIAL_GAP'
    involved_ids: Tuple[str, ...]
    geometry: Polygon
    area_m2: float
    perimeter_m: float
    thinness_ratio: float


class TopologyEngine:
    """
    Validates planar partition topology across adjacent land parcels or administrative units.
    """

    def __init__(self, sliver_max_width_m: float = 0.5, min_anomaly_area_m2: float = 0.05) -> None:
        self.sliver_max_width_m = sliver_max_width_m
        self.min_anomaly_area_m2 = min_anomaly_area_m2

    def detect_overlaps(
        self, parcel_ids: Sequence[str], polygons: Sequence[Polygon]
    ) -> List[TopologicalAnomaly]:
        """
        Detect all pairwise overlapping regions across a polygon dataset using an STRtree index.
        Classifies overlaps into 'SLIVER_OVERLAP' (digitization noise) vs 'OVERLAP' (substantive encroachment).
        """
        tree = STRtree(polygons)
        anomalies: List[TopologicalAnomaly] = []
        seen_pairs = set()

        for i, poly_i in enumerate(polygons):
            candidate_indices = tree.query(poly_i, predicate="intersects")
            for j in candidate_indices:
                j = int(j)
                if i >= j:
                    continue
                pair_key = (min(i, j), max(i, j))
                if pair_key in seen_pairs:
                    continue
                seen_pairs.add(pair_key)

                inter = poly_i.intersection(polygons[j])
                if inter.is_empty or inter.area < self.min_anomaly_area_m2:
                    continue

                polys = [inter] if isinstance(inter, Polygon) else (
                    list(inter.geoms) if hasattr(inter, "geoms") else []
                )
                for p in polys:
                    if isinstance(p, Polygon) and p.area >= self.min_anomaly_area_m2:
                        area = float(p.area)
                        perim = float(p.length)
                        approx_width = (2.0 * area) / max(perim, 1e-9)
                        thinness = (4.0 * 3.141592653589793 * area) / max(perim * perim, 1e-9)
                        atype = (
                            "SLIVER_OVERLAP"
                            if approx_width <= self.sliver_max_width_m
                            else "OVERLAP"
                        )
                        anomalies.append(
                            TopologicalAnomaly(
                                anomaly_type=atype,
                                involved_ids=(parcel_ids[i], parcel_ids[j]),
                                geometry=p,
                                area_m2=area,
                                perimeter_m=perim,
                                thinness_ratio=thinness,
                            )
                        )
        return anomalies

    def detect_interstitial_gaps(
        self, parcel_ids: Sequence[str], polygons: Sequence[Polygon], max_gap_width_m: float = 5.0
    ) -> List[TopologicalAnomaly]:
        """
        Identify unintended interstitial gaps (voids/slivers) between adjacent parcels
        using morphological dilation-erosion difference (closing minus union).
        """
        if not polygons:
            return []
        merged = unary_union(polygons)
        closed = merged.buffer(max_gap_width_m / 2.0, join_style="mitre").buffer(
            -max_gap_width_m / 2.0, join_style="mitre"
        )
        voids = closed.difference(merged)
        if voids.is_empty:
            return []

        void_polys = [voids] if isinstance(voids, Polygon) else (
            list(voids.geoms) if hasattr(voids, "geoms") else []
        )
        tree = STRtree(polygons)
        gaps: List[TopologicalAnomaly] = []

        for vp in void_polys:
            if not isinstance(vp, Polygon) or vp.area < self.min_anomaly_area_m2:
                continue
            touching_idxs = tree.query(vp.buffer(0.05), predicate="intersects")
            adj_ids = tuple(sorted({parcel_ids[int(k)] for k in touching_idxs}))
            if len(adj_ids) >= 2:
                area = float(vp.area)
                perim = float(vp.length)
                thinness = (4.0 * 3.141592653589793 * area) / max(perim * perim, 1e-9)
                gaps.append(
                    TopologicalAnomaly(
                        anomaly_type="INTERSTITIAL_GAP",
                        involved_ids=adj_ids,
                        geometry=vp,
                        area_m2=area,
                        perimeter_m=perim,
                        thinness_ratio=thinness,
                    )
                )
        return gaps
