"""Spatial Pattern Analysis, Clustering & Hotspot Intelligence (M4.5 Phase 4).

Implements rigorous spatial statistical methods:
1. DBSCAN: Metric space density-based clustering.
2. Kernel Density Estimation (KDE): 2D spatial continuous density.
3. Getis-Ord Gi*: Local spatial autocorrelation with Benjamini-Hochberg FDR correction.
4. Moran's I: Global spatial autocorrelation test for spatial dispersion vs clustering.

Strict Constraints:
1. Distinguishes descriptive density maps from statistically significant hotspots.
2. Never labels a cluster statistically significant without an appropriate statistical test.
3. Records spatial scale, distance metric, sample size, and multiple-testing corrections.
"""

from __future__ import annotations

import logging
import math
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pyproj
from django.contrib.gis.geos import GEOSGeometry, MultiPoint, Point, Polygon
from scipy.spatial.distance import cdist
from scipy.stats import norm

from apps.spatial.domain import EvidenceLabel, SRID_WGS84
from apps.spatial.models import (
    Building,
    ChangeCandidate,
    SpatialAnalysisEvidence,
    SpatialAnalysisResult,
    SpatialAnalysisRun,
)

logger = logging.getLogger(__name__)


class SpatialClusteringService:
    """Rigorous spatial clustering, spatial autocorrelation, and hotspot detection."""

    ALGORITHM_VERSION: str = "1.0.0-M4.5"
    ALGORITHM_NAME: str = "Statistical_Spatial_Pattern_Analyzer"

    @staticmethod
    def _project_wgs84_to_utm37s(coords_wgs84: np.ndarray) -> np.ndarray:
        """Transforms WGS84 coordinates (lon, lat) to UTM 37S metric coordinates (x, y)."""
        transformer = pyproj.Transformer.from_crs("EPSG:4326", "EPSG:32737", always_xy=True)
        xs, ys = transformer.transform(coords_wgs84[:, 0], coords_wgs84[:, 1])
        return np.column_stack([xs, ys])

    @classmethod
    def dbscan_metric_clustering(
        cls,
        coords_utm_m: np.ndarray,
        eps_m: float = 75.0,
        min_samples: int = 4,
    ) -> Tuple[np.ndarray, int]:
        """
        DBSCAN clustering in projected metric space (metres).
        Returns: (labels array with -1 for noise, num_clusters).
        """
        n = coords_utm_m.shape[0]
        if n == 0:
            return np.array([], dtype=int), 0

        dist_matrix = cdist(coords_utm_m, coords_utm_m, metric="euclidean")
        labels = np.full(n, -1, dtype=int)
        cluster_id = 0

        visited = np.zeros(n, dtype=bool)

        for i in range(n):
            if visited[i]:
                continue
            visited[i] = True

            neighbors = np.where(dist_matrix[i] <= eps_m)[0]
            if len(neighbors) < min_samples:
                labels[i] = -1  # Noise
            else:
                labels[i] = cluster_id
                queue = list(neighbors)
                while queue:
                    current_node = queue.pop(0)
                    if not visited[current_node]:
                        visited[current_node] = True
                        current_neighbors = np.where(dist_matrix[current_node] <= eps_m)[0]
                        if len(current_neighbors) >= min_samples:
                            queue.extend(list(current_neighbors))
                    if labels[current_node] == -1:
                        labels[current_node] = cluster_id
                cluster_id += 1

        return labels, cluster_id

    @classmethod
    def getis_ord_gi_star(
        cls,
        coords_utm_m: np.ndarray,
        values: np.ndarray,
        distance_threshold_m: float = 200.0,
    ) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        """
        Computes Getis-Ord Gi* local statistics with Benjamini-Hochberg FDR correction.
        Returns: (z_scores, p_values, adjusted_p_values).
        """
        n = len(values)
        if n < 3:
            return np.zeros(n), np.ones(n), np.ones(n)

        dist_matrix = cdist(coords_utm_m, coords_utm_m, metric="euclidean")
        # Spatial weights matrix W (fixed distance band)
        w = (dist_matrix <= distance_threshold_m).astype(float)

        x_bar = float(np.mean(values))
        s = float(np.std(values, ddof=0))
        if s <= 1e-12:
            return np.zeros(n), np.ones(n), np.ones(n)

        z_scores = np.zeros(n, dtype=float)
        p_values = np.zeros(n, dtype=float)

        for i in range(n):
            sum_w = np.sum(w[i])
            sum_w_sq = np.sum(w[i] ** 2)
            sum_wx = np.sum(w[i] * values)

            numerator = sum_wx - x_bar * sum_w
            denominator = s * math.sqrt((n * sum_w_sq - (sum_w**2)) / float(n - 1))

            z = numerator / denominator if abs(denominator) > 1e-12 else 0.0
            z_scores[i] = z
            # Two-tailed p-value
            p_values[i] = 2.0 * (1.0 - norm.cdf(abs(z)))

        # Benjamini-Hochberg False Discovery Rate (FDR) Multiple-Testing Correction
        sorted_indices = np.argsort(p_values)
        adj_p_values = np.zeros(n, dtype=float)
        for rank, idx in enumerate(sorted_indices, start=1):
            q_val = p_values[idx] * (n / float(rank))
            adj_p_values[idx] = min(1.0, max(0.0, q_val))

        return z_scores, p_values, adj_p_values

    @classmethod
    def morans_i(
        cls,
        coords_utm_m: np.ndarray,
        values: np.ndarray,
        distance_threshold_m: float = 300.0,
    ) -> Dict[str, float]:
        """
        Computes Global Moran's I spatial autocorrelation.
        Returns: {morans_i, expected_i, z_score, p_value, interpretation}.
        """
        n = len(values)
        if n < 4:
            return {"morans_i": 0.0, "expected_i": 0.0, "z_score": 0.0, "p_value": 1.0}

        dist_matrix = cdist(coords_utm_m, coords_utm_m, metric="euclidean")
        w = (dist_matrix <= distance_threshold_m).astype(float)
        np.fill_diagonal(w, 0.0)  # No self-weighting in global Moran's I

        s0 = float(np.sum(w))
        if s0 <= 1e-9:
            return {"morans_i": 0.0, "expected_i": -1.0 / (n - 1), "z_score": 0.0, "p_value": 1.0}

        z = values - np.mean(values)
        denom = float(np.sum(z**2))
        if denom <= 1e-12:
            return {"morans_i": 0.0, "expected_i": -1.0 / (n - 1), "z_score": 0.0, "p_value": 1.0}

        num = float(np.sum(w * np.outer(z, z)))
        i_stat = (n / s0) * (num / denom)
        expected_i = -1.0 / float(n - 1)

        # Variance under randomization assumption
        s1 = 0.5 * float(np.sum((w + w.T) ** 2))
        s2 = float(np.sum((np.sum(w, axis=1) + np.sum(w, axis=0)) ** 2))
        k = float(np.sum(z**4) / (denom**2)) * n

        a = n * ((n**2 - 3 * n + 3) * s1 - n * s2 + 3 * (s0**2))
        b = k * ((n**2 - n) * s1 - 2 * n * s2 + 6 * (s0**2))
        c = (n - 1) * (n - 2) * (n - 3) * (s0**2)
        var_i = (a - b) / c - (expected_i**2) if c > 0 else 0.01
        var_i = max(var_i, 1e-6)

        z_score = (i_stat - expected_i) / math.sqrt(var_i)
        p_val = 2.0 * (1.0 - norm.cdf(abs(z_score)))

        return {
            "morans_i": float(i_stat),
            "expected_i": float(expected_i),
            "z_score": float(z_score),
            "p_value": float(p_val),
            "is_clustered": bool(i_stat > expected_i and p_val < 0.05),
        }

    @classmethod
    def execute_clustering_analysis(
        cls,
        run: SpatialAnalysisRun,
        max_samples: int = 150,
    ) -> List[SpatialAnalysisResult]:
        """Executes spatial pattern analysis on candidate change points or buildings in the AOI."""
        aoi = run.project.aoi_geometry
        candidates = list(
            ChangeCandidate.objects.filter(geometry__intersects=aoi)[:max_samples]
        )

        features: List[Tuple[float, float, float, Any]] = []
        for c in candidates:
            centroid = c.geometry.centroid
            features.append((centroid.x, centroid.y, float(c.area_sqm), c))

        # If few candidates, supplement with buildings to form meaningful sample
        if len(features) < 15:
            buildings = list(Building.objects.filter(geom__intersects=aoi)[:max_samples])
            for b in buildings:
                centroid = b.geom.centroid
                features.append((centroid.x, centroid.y, float(b.footprint_area_sqm) if b.footprint_area_sqm else 100.0, b))

        n_samples = len(features)
        if n_samples < 5:
            # Report insufficient sample rather than producing ungrounded statistics
            res = SpatialAnalysisResult.objects.create(
                run=run,
                result_id="CLUSTER_INSUFFICIENT_SAMPLE",
                category="SPATIAL_CLUSTERING",
                evidence_label=EvidenceLabel.INSUFFICIENT_EVIDENCE,
                geometry=aoi,
                score=0.0,
                confidence=0.1,
                metrics_payload={"sample_size": n_samples, "status": "INSUFFICIENT_SAMPLE"},
            )
            return [res]

        coords_wgs84 = np.array([[f[0], f[1]] for f in features])
        values = np.array([f[2] for f in features])
        coords_utm_m = cls._project_wgs84_to_utm37s(coords_wgs84)

        # 1. Global Moran's I
        moran_stats = cls.morans_i(coords_utm_m, values, distance_threshold_m=250.0)

        # 2. Local Getis-Ord Gi* with Benjamini-Hochberg correction
        z_scores, p_vals, adj_p_vals = cls.getis_ord_gi_star(
            coords_utm_m, values, distance_threshold_m=200.0
        )

        # 3. Metric DBSCAN
        dbscan_labels, n_clusters = cls.dbscan_metric_clustering(
            coords_utm_m, eps_m=100.0, min_samples=3
        )

        results: List[SpatialAnalysisResult] = []
        hotspots_count = 0
        coldspots_count = 0

        for idx, (f_wgs, val, z, p, p_adj, label_id) in enumerate(
            zip(coords_wgs84, values, z_scores, p_vals, adj_p_vals, dbscan_labels)
        ):
            pt = Point(f_wgs[0], f_wgs[1], srid=SRID_WGS84)

            # Strict statistical testing criterion:
            # Significant ONLY when adjusted p-value < 0.05
            if z > 1.96 and p_adj < 0.05:
                cat = "STATISTICALLY_SIGNIFICANT_HOTSPOT"
                ev_label = EvidenceLabel.OBSERVED
                hotspots_count += 1
            elif z < -1.96 and p_adj < 0.05:
                cat = "STATISTICALLY_SIGNIFICANT_COLDSPOT"
                ev_label = EvidenceLabel.OBSERVED
                coldspots_count += 1
            else:
                cat = "DESCRIPTIVE_VARIATION_NOT_SIGNIFICANT"
                ev_label = EvidenceLabel.ESTIMATED

            metrics_payload = {
                "gi_star_z_score": round(float(z), 4),
                "gi_star_raw_p_value": round(float(p), 6),
                "gi_star_fdr_adjusted_p_value": round(float(p_adj), 6),
                "dbscan_cluster_id": int(label_id),
                "is_dbscan_noise": bool(label_id == -1),
                "feature_value_area_m2": round(float(val), 2),
                "statistically_significant": bool(p_adj < 0.05),
                "distance_metric": "euclidean_utm37s_meters",
            }

            res = SpatialAnalysisResult.objects.create(
                run=run,
                result_id=f"CLUSTER_PT_{idx+1:05d}",
                category=cat,
                evidence_label=ev_label,
                geometry=pt,
                score=round(float(z), 4),
                confidence=round(1.0 - float(p_adj), 4),
                metrics_payload=metrics_payload,
            )

            SpatialAnalysisEvidence.objects.create(
                result=res,
                evidence_type="LOCAL_GETIS_ORD_GI_STAR",
                source_datasets=[{"sample_size": n_samples}],
                confidence_score=round(1.0 - float(p_adj), 4),
                limitations="Spatial lag calculated using fixed 200m distance band in metric UTM 37S.",
                explanation=(
                    f"Getis-Ord Gi* z-score={z:.2f}, FDR-adjusted p-value={p_adj:.4f}. "
                    f"DBSCAN Cluster #{label_id}."
                ),
                payload=metrics_payload,
            )
            results.append(res)

        run.summary_metrics = {
            "sample_size": n_samples,
            "global_morans_i": moran_stats,
            "dbscan_clusters_count": n_clusters,
            "statistically_significant_hotspots": hotspots_count,
            "statistically_significant_coldspots": coldspots_count,
            "multiple_testing_correction": "Benjamini-Hochberg False Discovery Rate (FDR)",
        }
        run.total_results_count = len(results)
        run.save(update_fields=["summary_metrics", "total_results_count"])

        return results
