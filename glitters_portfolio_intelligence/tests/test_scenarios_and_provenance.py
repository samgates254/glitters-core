from __future__ import annotations

import unittest

from glitters_portfolio_intelligence.domain.entities import PropertyAsset
from glitters_portfolio_intelligence.domain.enums import (
    AssetCategory,
    GeographicSubmarket,
    ScenarioProvenanceKind,
)
from glitters_portfolio_intelligence.scenarios.provenance import ScenarioDefinition
from glitters_portfolio_intelligence.scenarios.stress_engine import ScenarioStressEngine


class TestScenariosAndProvenance(unittest.TestCase):
    def test_canonical_9_scenarios_and_probabilities(self):
        scenarios = ScenarioStressEngine.get_canonical_scenarios()
        self.assertEqual(len(scenarios), 9)

        # Probabilities sum to 1.0 (or close before normalization)
        total_p = sum(s.probability for s in scenarios)
        self.assertAlmostEqual(total_p, 1.0, places=2)

        # Check all required scenario IDs are present
        scen_ids = {s.scenario_id for s in scenarios}
        expected_ids = {
            "SCEN_BASE",
            "SCEN_INFLATION",
            "SCEN_RATES",
            "SCEN_ASSET_FALL",
            "SCEN_VACANCY",
            "SCEN_STAGNATION",
            "SCEN_CONSTRUCTION",
            "SCEN_REGIONAL_DETER",
            "SCEN_LIQUIDITY_CRUNCH",
        }
        self.assertEqual(scen_ids, expected_ids)

    def test_provenance_integrity_validation(self):
        # Valid historical scenario
        scen_hist = ScenarioDefinition(
            scenario_id="HIST-01",
            name="Valid Historical Replay",
            provenance=ScenarioProvenanceKind.HISTORICAL_OBSERVATION,
            source="KNBS Official Census 2024",
            observation_or_forecast_period="2020-2022",
            probability=0.10,
            model_version="v1",
            transformation_methodology="Historical empirical replay",
        )
        self.assertEqual(scen_hist.provenance, ScenarioProvenanceKind.HISTORICAL_OBSERVATION)

        # Invalid: claiming historical observation with synthetic methodology
        with self.assertRaises(ValueError):
            ScenarioDefinition(
                scenario_id="INVALID-01",
                name="Bogus Historical",
                provenance=ScenarioProvenanceKind.HISTORICAL_OBSERVATION,
                source="Imaginary dataset",
                observation_or_forecast_period="2026",
                probability=0.10,
                model_version="v1",
                transformation_methodology="Synthetic shock generation",
            )


if __name__ == "__main__":
    unittest.main()
