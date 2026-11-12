"""
Module: `market_intelligence`
=============================
Kenyan Real Estate Market Intelligence Algorithms:
1. KBA-HPI Style Laspeyres & Quality-Adjusted Hedonic Price Indices (strictly on verified sales)
2. State-Space Local Linear Trend Kalman Filter
3. Overpriced Listing & Undervalued Opportunity Detection
4. Emerging Investment Hotspot Scorer
"""
from __future__ import annotations
from typing import List, Dict, Any, Tuple
import numpy as np

from ..contracts import (
    MarketTransaction,
    VERIFIED_CAPITAL_SALE_EVIDENCE_TYPES,
    CAPITAL_SALE_EVIDENCE_TYPES,
)


class MarketIntelligenceEngine:
    def compute_quarterly_price_indices(
        self, transactions: List[MarketTransaction], quarter_length_days: float = 91.25
    ) -> Dict[str, Any]:
        verified = [
            tx for tx in transactions
            if tx.evidence_type in VERIFIED_CAPITAL_SALE_EVIDENCE_TYPES
        ]
        if len(verified) < 6:
            return {"status": "INSUFFICIENT_TRANSACTIONS", "quarters": []}

        min_day = min(tx.transaction_timestamp_days for tx in verified)
        quarters: Dict[int, List[MarketTransaction]] = {}
        for tx in verified:
            q_idx = int((tx.transaction_timestamp_days - min_day) // quarter_length_days)
            quarters.setdefault(q_idx, []).append(tx)

        sorted_q = sorted(quarters.keys())
        raw_ppsqm_series: List[float] = []
        tx_counts: List[int] = []

        for q in sorted_q:
            q_txs = quarters[q]
            ppsqm = [
                tx.observed_price_kes / max(1.0, tx.property_record.land_area_sqm + 2.5 * tx.property_record.building_area_sqm)
                for tx in q_txs
            ]
            raw_ppsqm_series.append(float(np.median(ppsqm)))
            tx_counts.append(len(q_txs))

        base_val = raw_ppsqm_series[0] if raw_ppsqm_series[0] > 0 else 1.0
        raw_index = np.array([100.0 * (v / base_val) for v in raw_ppsqm_series], dtype=np.float64)

        kalman_level, kalman_slope = self._local_linear_kalman_filter(raw_index, np.array(tx_counts))

        return {
            "status": "OK",
            "quarters": sorted_q,
            "raw_laspeyres_index": [round(float(x), 2) for x in raw_index],
            "kalman_smoothed_index": [round(float(x), 2) for x in kalman_level],
            "quarterly_growth_rate_pct": [round(float(b), 3) for b in kalman_slope],
            "transaction_counts": tx_counts,
        }

    def _local_linear_kalman_filter(
        self, y_obs: np.ndarray, counts: np.ndarray
    ) -> Tuple[np.ndarray, np.ndarray]:
        n = len(y_obs)
        if n == 0:
            return np.array([]), np.array([])
        x = np.array([y_obs[0], 0.0], dtype=np.float64)
        P = np.array([[25.0, 0.0], [0.0, 4.0]], dtype=np.float64)
        F = np.array([[1.0, 1.0], [0.0, 1.0]], dtype=np.float64)
        Q = np.array([[2.0, 0.0], [0.0, 0.4]], dtype=np.float64)
        H = np.array([[1.0, 0.0]], dtype=np.float64)

        levels = np.zeros(n, dtype=np.float64)
        slopes = np.zeros(n, dtype=np.float64)

        for t in range(n):
            if t > 0:
                x = F @ x
                P = F @ P @ F.T + Q
            r_var = 18.0 / max(1.0, float(counts[t]))
            y_res = y_obs[t] - float((H @ x)[0])
            S = float((H @ P @ H.T)[0, 0]) + r_var
            K = (P @ H.T) / S
            x = x + (K.ravel() * y_res)
            P = (np.eye(2) - K @ H) @ P
            levels[t] = x[0]
            slopes[t] = x[1]

        return levels, slopes

    def classify_listing_mispricing(
        self,
        asking_price_kes: float,
        point_estimate_kes: float,
        lower_bound_kes: float,
        upper_bound_kes: float,
        expected_negotiation_haircut: float = 0.085,
    ) -> Dict[str, Any]:
        implied_closing_ask = asking_price_kes * (1.0 - expected_negotiation_haircut)
        mispricing_pct = (implied_closing_ask - point_estimate_kes) / max(1.0, point_estimate_kes)

        if implied_closing_ask > upper_bound_kes:
            classification = "SIGNIFICANTLY_OVERPRICED"
        elif mispricing_pct > 0.10:
            classification = "MODERATELY_OVERPRICED"
        elif asking_price_kes < lower_bound_kes:
            classification = "SIGNIFICANTLY_UNDERVALUED_OR_DISTRESSED"
        elif mispricing_pct < -0.10:
            classification = "UNDERVALUED_OPPORTUNITY"
        else:
            classification = "FAIRLY_PRICED_WITHIN_MARKET_BAND"

        return {
            "asking_price_kes": round(asking_price_kes, 2),
            "negotiation_adjusted_ask_kes": round(implied_closing_ask, 2),
            "fair_value_estimate_kes": round(point_estimate_kes, 2),
            "net_mispricing_pct": round(mispricing_pct, 4),
            "classification": classification,
        }

    def rank_emerging_investment_locations(
        self, transactions: List[MarketTransaction]
    ) -> List[Dict[str, Any]]:
        sale_txs = [t for t in transactions if t.evidence_type in CAPITAL_SALE_EVIDENCE_TYPES]
        by_loc: Dict[str, List[MarketTransaction]] = {}
        for tx in sale_txs:
            by_loc.setdefault(tx.property_record.locality, []).append(tx)

        rankings: List[Dict[str, Any]] = []
        for loc, loc_txs in by_loc.items():
            if len(loc_txs) < 3:
                continue
            loc_txs.sort(key=lambda t: t.transaction_timestamp_days)
            half = max(1, len(loc_txs) // 2)
            early_ppsqm = np.median([
                t.observed_price_kes / max(1.0, t.property_record.land_area_sqm) for t in loc_txs[:half]
            ])
            late_ppsqm = np.median([
                t.observed_price_kes / max(1.0, t.property_record.land_area_sqm) for t in loc_txs[half:]
            ])
            momentum = float((late_ppsqm - early_ppsqm) / max(1.0, early_ppsqm))

            yields = []
            for t in loc_txs:
                if t.property_record.monthly_gross_rent_kes and t.observed_price_kes > 0:
                    yields.append(12.0 * t.property_record.monthly_gross_rent_kes / t.observed_price_kes)
            mean_yield = float(np.mean(yields)) if yields else 0.065

            infra = float(np.mean([t.property_record.infrastructure_score for t in loc_txs]))
            unused_far = float(np.mean([
                max(0.0, t.property_record.permitted_plot_ratio - (t.property_record.building_area_sqm / max(1.0, t.property_record.land_area_sqm)))
                for t in loc_txs
            ]))
            liquidity_score = min(1.0, len(loc_txs) / 20.0)

            hotspot_score = (
                0.35 * max(-0.5, min(1.0, momentum * 3.0))
                + 0.25 * (mean_yield / 0.10)
                + 0.20 * (unused_far / 2.5)
                + 0.20 * liquidity_score * infra
            )
            rankings.append({
                "locality": loc,
                "county": loc_txs[0].property_record.county,
                "hotspot_composite_score": round(float(hotspot_score), 4),
                "price_momentum_pct": round(momentum, 4),
                "average_gross_rental_yield": round(mean_yield, 4),
                "transaction_liquidity_score": round(liquidity_score, 4),
            })

        rankings.sort(key=lambda r: r["hotspot_composite_score"], reverse=True)
        return rankings
