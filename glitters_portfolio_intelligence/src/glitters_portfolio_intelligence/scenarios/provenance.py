from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import os
from pathlib import Path
from typing import Dict, Mapping, Optional

from ..domain.enums import ScenarioProvenanceKind


class EvidenceArtifactRegistry:
    """
    Independent repository/verifier for authenticating historical dataset evidence artifacts.
    Enforces that dataset SHA256 hashes correspond to actual verified content,
    preventing arbitrary unverified strings from bypassing epistemic governance.
    """

    def __init__(self) -> None:
        self._artifacts: dict[str, bytes] = {}
        self._artifact_files: dict[str, Path] = {}
        self._verified_digests: dict[str, str] = {}

    def register_artifact(
        self,
        dataset_identifier: str,
        content: bytes | str,
    ) -> str:
        """Registers in-memory artifact content and computes its canonical SHA-256 digest."""
        if not dataset_identifier:
            raise ValueError("dataset_identifier cannot be empty")
        data = content.encode("utf-8") if isinstance(content, str) else content
        digest = hashlib.sha256(data).hexdigest()
        self._artifacts[dataset_identifier] = data
        self._verified_digests[dataset_identifier] = digest
        return digest

    def register_file(self, dataset_identifier: str, file_path: str | Path) -> str:
        """Registers a filesystem artifact and computes its SHA-256 digest from actual content."""
        p = Path(file_path)
        if not p.is_file():
            raise FileNotFoundError(f"Evidence artifact file does not exist: {file_path}")
        data = p.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        self._artifact_files[dataset_identifier] = p
        self._artifacts[dataset_identifier] = data
        self._verified_digests[dataset_identifier] = digest
        return digest

    def is_artifact_authenticated(
        self,
        dataset_identifier: Optional[str],
        declared_sha256: Optional[str],
    ) -> bool:
        """
        Returns True if and only if:
          1. The dataset_identifier is registered in this registry.
          2. The artifact's actual content SHA-256 matches declared_sha256 exactly.
        """
        if not dataset_identifier or not declared_sha256:
            return False
        if dataset_identifier not in self._verified_digests:
            return False
        actual_digest = self._verified_digests[dataset_identifier]
        return actual_digest.lower() == declared_sha256.strip().lower()

    def get_actual_digest(self, dataset_identifier: str) -> Optional[str]:
        return self._verified_digests.get(dataset_identifier)

    def has_authenticated_artifacts(self) -> bool:
        """Returns True if at least one authenticated evidence artifact is registered."""
        return len(self._verified_digests) > 0

    @property
    def registered_identifiers(self) -> tuple[str, ...]:
        return tuple(self._verified_digests.keys())


default_evidence_registry = EvidenceArtifactRegistry()


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
    def has_declared_evidence_parameters(self) -> bool:
        """
        True if this scenario declares both a dataset identifier and a syntactically valid SHA256 string.
        Note: Syntax presence does NOT imply authentication; use is_evidence_authenticated().
        """
        return bool(
            self.provenance == ScenarioProvenanceKind.HISTORICAL_OBSERVATION
            and self.dataset_identifier
            and self.dataset_sha256
            and len(self.dataset_sha256) == 64
        )

    def is_evidence_authenticated(
        self,
        registry: Optional[EvidenceArtifactRegistry] = None,
    ) -> bool:
        """
        Validates that the evidence artifact actually exists and matches its content digest.
        Arbitrary unverified strings or unregistered datasets return False.
        """
        if self.provenance != ScenarioProvenanceKind.HISTORICAL_OBSERVATION:
            return False
        reg = registry if registry is not None else default_evidence_registry
        return reg.is_artifact_authenticated(self.dataset_identifier, self.dataset_sha256)

    @property
    def has_verified_historical_evidence(self) -> bool:
        """Backwards compatibility alias for authenticated evidence."""
        return self.is_evidence_authenticated()

    def reclassify_if_unverified(
        self,
        registry: Optional[EvidenceArtifactRegistry] = None,
    ) -> ScenarioDefinition:
        """
        Where source evidence cannot be authenticated against actual content,
        reclassifies the scenario as EXPERT_ASSUMPTION so unverified scenarios do not
        claim historical validation while preserving the original assumptions and methodology.
        """
        if self.provenance == ScenarioProvenanceKind.HISTORICAL_OBSERVATION and not self.is_evidence_authenticated(registry):
            from dataclasses import replace
            return replace(
                self,
                provenance=ScenarioProvenanceKind.EXPERT_ASSUMPTION,
                transformation_methodology=f"{self.transformation_methodology} (Reclassified: unauthenticated historical evidence)",
            )
        return self
