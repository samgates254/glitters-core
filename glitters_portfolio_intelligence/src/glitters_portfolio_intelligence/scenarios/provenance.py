from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Mapping, Optional

from ..domain.enums import ScenarioProvenanceKind


@dataclass(frozen=True, slots=True)
class ScenarioDefinition:
    """
    Rigorously tracked scenario definition ensuring full provenance transparency.
    Forbids mislabeling arbitrary shocks as verified historical facts.
    Requires verified dataset identifier and SHA256 evidence artifact for historical scenarios.
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
    dataset_identifier: Optional[str] = None
    dataset_sha256: Optional[str] = None

    def __post_init__(self) -> None:
        if not (0.0 <= self.probability <= 1.0):
            raise ValueError(f"Scenario probability must be in [0, 1], got {self.probability}")
        if not self.scenario_id or not self.name:
            raise ValueError("Scenario ID and name cannot be empty")
        if not self.source:
            raise ValueError("Scenario source must be explicitly declared for auditability")

        if self.dataset_sha256 is not None:
            if len(self.dataset_sha256) != 64 or not all(c in "0123456789abcdefABCDEF" for c in self.dataset_sha256):
                raise ValueError(f"dataset_sha256 must be a valid 64-character hex string: {self.dataset_sha256}")

        # Provenance integrity validation
        if self.provenance == ScenarioProvenanceKind.HISTORICAL_OBSERVATION:
            if "synthetic" in self.transformation_methodology.lower() or "hypothetical" in self.source.lower():
                raise ValueError("HISTORICAL_OBSERVATION cannot use synthetic or hypothetical transformation methodology")
        elif self.provenance == ScenarioProvenanceKind.SYNTHETIC_SCENARIO:
            if "actual historical" in self.source.lower():
                raise ValueError("SYNTHETIC_SCENARIO cannot claim actual historical source")

    @property
    def has_verified_historical_evidence(self) -> bool:
        """
        True only if this is a HISTORICAL_OBSERVATION scenario with both a declared
        dataset identifier and a valid 64-character SHA256 evidence artifact.
        """
        return bool(
            self.provenance == ScenarioProvenanceKind.HISTORICAL_OBSERVATION
            and self.dataset_identifier
            and self.dataset_sha256
            and len(self.dataset_sha256) == 64
        )

    def reclassify_if_unverified(self) -> ScenarioDefinition:
        """
        Where source evidence (dataset_identifier and valid dataset_sha256) is unavailable,
        reclassifies the scenario as EXPERT_ASSUMPTION so unverified scenarios do not
        claim historical validation while preserving the original assumptions and methodology.
        """
        if self.provenance == ScenarioProvenanceKind.HISTORICAL_OBSERVATION and not self.has_verified_historical_evidence:
            from dataclasses import replace
            return replace(
                self,
                provenance=ScenarioProvenanceKind.EXPERT_ASSUMPTION,
                transformation_methodology=f"{self.transformation_methodology} (Reclassified: unverified historical evidence)",
            )
        return self
