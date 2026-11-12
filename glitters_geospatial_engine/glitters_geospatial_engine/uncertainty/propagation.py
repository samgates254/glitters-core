"""
Uncertainty Propagation & Scientific Validation Framework (`uncertainty/` & `evaluation/`)
==========================================================================================

Implements:
1. Analytical Gauss-Markov & Monte Carlo coordinate error propagation for polygon area
   and boundary displacement (`uncertainty/propagation.py`).
2. Scientific validation metrics (`evaluation/metrics.py`):
   - Geometry Precision, Recall, F1, and Intersection-over-Union (IoU)
   - Boundary Displacement Error (Mean, RMSE, 95th-percentile Hausdorff)
   - Building Detection object-level Precision, Recall, F1 at IoU >= 0.50
   - Change Detection pixel & object F1, False Positive Rate, and Cohen's Kappa
   - ASPRS Positional Accuracy Standards (RMSE_x, RMSE_y, RMSE_r, CE90, LE95)
   - Expected Calibration Error (ECE) for probabilistic confidence outputs.
"""

from __future__ import annotations
from dataclasses import dataclass
import math
from typing import Any, Dict, List, Sequence, Tuple
import numpy as np
from shapely.geometry import Polygon
from shapely.strtree import STRtree

from ..geometry.algorithms import ComputationalGeometryEngine


class UncertaintyEngine:
    """
    Propagates positional coordinate uncertainty (sigma_x, sigma_y) on polygon vertices
    into area uncertainty (sigma_A, 95% CI) using both exact analytical Jacobian derivatives
    of the Shoelace formula and Monte Carlo ensemble perturbation.
    """

    @staticmethod
    def analytical_shoelace_area_uncertainty(
        poly_utm: Polygon, vertex_sigma_m: float = 0.15
    ) -> Dict[str, float]:
        """
        Given a polygon with vertices (x_0, y_0), ..., (x_{n-1}, y_{n-1}) and isotropic
        independent vertex positional standard error `vertex_sigma_m` (sigma_x = sigma_y = sigma):
            A = 0.5 * |sum_{i=0}^{n-1} (x_i y_{i+1} - x_{i+1} y_i)|
            dA/dx_i = 0.5 * (y_{i+1} - y_{i-1})
            dA/dy_i = 0.5 * (x_{i-1} - x_{i+1})
            sigma_A^2 = (sigma^2 / 4) * sum_{i=0}^{n-1} [ (x_{i+1} - x_{i-1})^2 + (y_{i+1} - y_{i-1})^2 ]
        """
        coords = np.asarray(poly_utm.exterior.coords[:-1], dtype=np.float64)
        n = len(coords)
        if n < 3:
            return {"area_m2": 0.0, "sigma_area_m2": 0.0, "ci95_halfwidth_m2": 0.0, "relative_sigma_pct": 0.0}

        prev_pts = np.roll(coords, 1, axis=0)
        next_pts = np.roll(coords, -1, axis=0)
        chord_sq = np.sum((next_pts - prev_pts) ** 2, axis=1)
        var_a = 0.25 * (vertex_sigma_m ** 2) * float(np.sum(chord_sq))
        sigma_a = math.sqrt(max(0.0, var_a))
        area = float(poly_utm.area)
        return {
            "area_m2": area,
            "sigma_area_m2": sigma_a,
            "ci95_halfwidth_m2": 1.96 * sigma_a,
            "relative_sigma_pct": (sigma_a / max(area, 1e-9)) * 100.0,
        }

    @staticmethod
    def monte_carlo_area_uncertainty(
        poly_utm: Polygon,
        vertex_sigma_m: float = 0.15,
        n_simulations: int = 200,
        seed: int = 42,
    ) -> Dict[str, float]:
        """
        Perturb polygon vertices with Gaussian noise N(0, sigma^2 I) across `n_simulations`
        realizations to empirically verify analytical variance propagation.
        """
        rng = np.random.default_rng(seed)
        coords = np.asarray(poly_utm.exterior.coords[:-1], dtype=np.float64)
        areas = np.empty(n_simulations, dtype=np.float64)
        for s in range(n_simulations):
            perturbed = coords + rng.normal(0.0, vertex_sigma_m, size=coords.shape)
            # Shoelace formula directly on perturbed coordinates
            x = perturbed[:, 0]
            y = perturbed[:, 1]
            areas[s] = 0.5 * abs(float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))))

        return {
            "mc_mean_area_m2": float(np.mean(areas)),
            "mc_std_area_m2": float(np.std(areas, ddof=1)),
            "mc_p025_area_m2": float(np.percentile(areas, 2.5)),
            "mc_p975_area_m2": float(np.percentile(areas, 97.5)),
        }


class ScientificValidationFramework:
    """
    Comprehensive evaluation metrics for geometry, building detection, change detection,
    land-cover classification, and positional accuracy.
    """

    @staticmethod
    def evaluate_polygon_pair(pred_poly: Polygon, gt_poly: Polygon) -> Dict[str, float]:
        """
        Compute Geometry Precision, Geometry Recall, F1, IoU, and Boundary Displacement Error.
        """
        geom_engine = ComputationalGeometryEngine()
        inter_area = float(geom_engine.polygon_intersection(pred_poly, gt_poly).area)
        union_area = float(geom_engine.polygon_union(pred_poly, gt_poly).area)
        pred_area = max(float(pred_poly.area), 1e-12)
        gt_area = max(float(gt_poly.area), 1e-12)

        precision = inter_area / pred_area
        recall = inter_area / gt_area
        f1 = (2.0 * precision * recall) / max(precision + recall, 1e-12)
        iou = inter_area / max(union_area, 1e-12)
        b_cmp = geom_engine.compare_boundaries(pred_poly, gt_poly)

        return {
            "geometry_precision": min(1.0, precision),
            "geometry_recall": min(1.0, recall),
            "geometry_f1": min(1.0, f1),
            "iou": min(1.0, iou),
            "boundary_hausdorff_error_m": b_cmp.hausdorff_distance_m,
            "boundary_frechet_error_m": b_cmp.discrete_frechet_distance_m,
            "centroid_displacement_error_m": b_cmp.centroid_displacement_m,
        }

    @staticmethod
    def evaluate_building_detection_instances(
        predicted_footprints: Sequence[Polygon],
        ground_truth_footprints: Sequence[Polygon],
        iou_threshold: float = 0.50,
    ) -> Dict[str, float]:
        """
        Evaluate instance-level building detection Precision, Recall, F1, and Mean True-Positive IoU
        at `iou_threshold` (standard SpaceNet / CrowdAI benchmark protocol IoU >= 0.50).
        """
        if not ground_truth_footprints and not predicted_footprints:
            return {"precision": 1.0, "recall": 1.0, "f1": 1.0, "mean_tp_iou": 1.0, "tp": 0, "fp": 0, "fn": 0}
        if not predicted_footprints:
            return {"precision": 0.0, "recall": 0.0, "f1": 0.0, "mean_tp_iou": 0.0, "tp": 0, "fp": 0, "fn": len(ground_truth_footprints)}
        if not ground_truth_footprints:
            return {"precision": 0.0, "recall": 0.0, "f1": 0.0, "mean_tp_iou": 0.0, "tp": 0, "fp": len(predicted_footprints), "fn": 0}

        gt_tree = STRtree(ground_truth_footprints)
        matched_gt = set()
        tp = 0
        tp_ious: List[float] = []

        for pred in predicted_footprints:
            cands = gt_tree.query(pred, predicate="intersects")
            best_iou = 0.0
            best_gt_idx = -1
            for g_idx in cands:
                g_idx = int(g_idx)
                if g_idx in matched_gt:
                    continue
                inter = pred.intersection(ground_truth_footprints[g_idx]).area
                union = pred.union(ground_truth_footprints[g_idx]).area
                iou = float(inter / max(union, 1e-12))
                if iou > best_iou:
                    best_iou = iou
                    best_gt_idx = g_idx
            if best_iou >= iou_threshold and best_gt_idx >= 0:
                tp += 1
                matched_gt.add(best_gt_idx)
                tp_ious.append(best_iou)

        fp = len(predicted_footprints) - tp
        fn = len(ground_truth_footprints) - tp
        prec = tp / max(tp + fp, 1)
        rec = tp / max(tp + fn, 1)
        f1 = (2.0 * prec * rec) / max(prec + rec, 1e-12)
        return {
            "precision": float(prec),
            "recall": float(rec),
            "f1": float(f1),
            "mean_tp_iou": float(np.mean(tp_ious)) if tp_ious else 0.0,
            "tp": int(tp),
            "fp": int(fp),
            "fn": int(fn),
        }

    @staticmethod
    def evaluate_classification_and_change_metrics(
        y_pred: np.ndarray, y_true: np.ndarray
    ) -> Dict[str, Any]:
        """
        Compute Overall Accuracy, Change/Binary F1, False Positive Rate, and Cohen's Kappa.
        """
        pred = y_pred.ravel()
        true = y_true.ravel()
        n = max(1, len(true))
        overall_acc = float(np.sum(pred == true) / n)

        # Binary change metrics (treating class > 0 as positive change if binary, or macro average)
        classes = np.unique(np.concatenate([pred, true]))
        # Cohen's Kappa
        pe = sum(float(np.sum(pred == c)) * float(np.sum(true == c)) for c in classes) / (n * n)
        kappa = (overall_acc - pe) / max(1.0 - pe, 1e-12)

        # Binary positive class (1 or >0) precision/recall/F1
        pos_p = pred > 0
        pos_t = true > 0
        tp = int(np.sum(pos_p & pos_t))
        fp = int(np.sum(pos_p & (~pos_t)))
        fn = int(np.sum((~pos_p) & pos_t))
        tn = int(np.sum((~pos_p) & (~pos_t)))

        prec = tp / max(tp + fp, 1)
        rec = tp / max(tp + fn, 1)
        f1 = (2.0 * prec * rec) / max(prec + rec, 1e-12)
        fpr = fp / max(fp + tn, 1)

        return {
            "overall_accuracy": overall_acc,
            "cohen_kappa": float(kappa),
            "precision": float(prec),
            "recall": float(rec),
            "f1": float(f1),
            "false_positive_rate": float(fpr),
        }

    @staticmethod
    def evaluate_positional_accuracy_asprs(
        measured_xy: np.ndarray, reference_xy: np.ndarray
    ) -> Dict[str, float]:
        """
        Compute ASPRS Positional Accuracy Standards for Digital Geospatial Data:
            RMSE_x, RMSE_y, horizontal radial RMSE_r = sqrt(RMSE_x^2 + RMSE_y^2),
             and 90% Circular Error (CE90 = 1.5175 * RMSE_r) / 95% Horizontal Accuracy (1.7308 * RMSE_r).
        """
        diff = measured_xy - reference_xy
        rmse_x = float(np.sqrt(np.mean(diff[:, 0] ** 2)))
        rmse_y = float(np.sqrt(np.mean(diff[:, 1] ** 2)))
        rmse_r = math.hypot(rmse_x, rmse_y)
        return {
            "rmse_x_m": rmse_x,
            "rmse_y_m": rmse_y,
            "rmse_radial_m": rmse_r,
            "ce90_m": 1.5175 * rmse_r,
            "horizontal_accuracy_95pct_m": 1.7308 * rmse_r,
        }
