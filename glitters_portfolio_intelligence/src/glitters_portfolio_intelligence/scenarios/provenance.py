from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Mapping, Optional

from ..domain.enums import ScenarioProvenanceKind


@dataclass(frozen=True, slots=True)
class ScenarioDefinition:
    """
    Rigorously tracked scenario definition ensuring full provenance transparency.
    Forbids mislabeling arbitrary shocks as verified historical facts.
    """
    scenario_id: str
    name: str
    provenance: ScenarioProvenanceKind
    source: str
    observation_or_forecast_period: str
    probability: float
    model_version: str
    transformation_methodology: str
    assumptions: Mapping[str, float] = field(default_factory=dict)
    asset_returns: Mapping[str, float] = field(default_factory=dict)  # asset_id -> annual return

    def __post_init__(self) -> None:
        if not (0.0 <= self.probability <= 1.0):
            raise ValueError(f"Scenario probability must be in [0, 1], got {self.probability}")
        if not self.scenario_id or not self.name:
            raise ValueError("Scenario ID and name cannot be empty")
        if not self.source:
            raise ValueError("Scenario source must be explicitly declared for auditability")

        # Provenance integrity validation
        if self.provenance == ScenarioProvenanceKind.HISTORICAL_OBSERVATION:
            if "synthetic" in self.transformation_methodology.lower() or "hypothetical" in self.source.lower():
                raise ValueError("HISTORICAL_OBSERVATION cannot use synthetic or hypothetical transformation methodology")
        elif self.provenance == ScenarioProvenanceKind.SYNTHETIC_SCENARIO:
            if "actual historical" in self.source.lower():
                raise ValueError("SYNTHETIC_SCENARIO cannot claim actual historical source")
