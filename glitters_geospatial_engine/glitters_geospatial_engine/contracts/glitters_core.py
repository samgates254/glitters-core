"""
Integration Contracts for GLITTERS CORE (`contracts/glitters_core.py` v1.1.0)
=============================================================================

Upgrades in v1.1.0:
1. Includes `EPSG:21096` (Arc 1960 / UTM Zone 36N) in `SUPPORTED_KENYA_METRIC_EPSG_CODES`
   and enforces `KenyaCRSEngine.assert_projected_crs(self.projected_utm_epsg, require_kenya_epsg=True)`.
2. Requires a cryptographically validated `CadastralVerificationEvidence` instance whenever
   `is_legally_verified_cadastre=True` (rejecting bare boolean/enum claims).
3. Uses honest spatial key naming (`glitters_hex_res9` and `glitters_hilbert_lvl16`) alongside
   explicit `grid_standards` metadata.
"""

from __future__ import annotations
from dataclasses import dataclass, field
import json
from typing import Any, Dict, Optional, Tuple

from ..crs.kenya_crs import KenyaCRSEngine, SUPPORTED_KENYA_METRIC_EPSG_CODES
from ..parcels.intelligence import CadastralVerificationEvidence
from ..spatial_features.feature_vector import PropertySpatialFeatureVector


class ContractComplianceViolationError(ValueError):
    """Raised when a geospatial intelligence payload violates GLITTERS non-negotiable rules."""


@dataclass(frozen=True)
class GlittersCoreParcelIntelligencePayload:
    """
    Immutable integration contract emitted to GLITTERS CORE for property operations,
    valuation features, construction monitoring, and spatial search indexing.
    """
    contract_version: str
    event_id: str
    timestamp_iso: str
    property_id: str
    parcel_id: str
    wgs84_geojson_geometry: Dict[str, Any]
    projected_utm_epsg: int
    glitters_hex_res9: str
    glitters_hilbert_lvl16: int
    geohash_8: str
    observation_authority: str
    is_legally_verified_cadastre: bool
    inferred_legal_owner: Optional[str]  # MUST always be None for imagery/geometric inference
    spatial_feature_vector: PropertySpatialFeatureVector
    cadastral_verification_evidence: Optional[CadastralVerificationEvidence] = None
    tenant_org_id: Optional[str] = None
    detected_changes: Tuple[Dict[str, Any], ...] = field(default_factory=tuple)
    topological_warnings: Tuple[str, ...] = field(default_factory=tuple)

    def validate_contract_invariants(self) -> bool:
        """
        Runtime contract gatekeeper enforcing GLITTERS Non-Negotiable Rules:
        1. Never claim legal ownership from imagery or geometry (`inferred_legal_owner` must be None).
        2. Never present inferred boundaries as legally authoritative cadastre; if
           `is_legally_verified_cadastre=True`, require validated `CadastralVerificationEvidence`.
        3. Enforce SI metre projected Kenya UTM CRS (`SUPPORTED_KENYA_METRIC_EPSG_CODES`).
        4. Require explicit observation mask and 1-sigma uncertainty alignment.
        """
        if self.inferred_legal_owner is not None:
            raise ContractComplianceViolationError(
                "Rule 3 Violation: Legal property ownership must NEVER be inferred from "
                "satellite imagery, parcel geometry, or spatial proximity."
            )
        if self.is_legally_verified_cadastre:
            if self.observation_authority != "LICENSED_SURVEY_PLAN_REFERENCE":
                raise ContractComplianceViolationError(
                    "Rule 2 Violation: Inferred or public parcel boundaries must NEVER be marked "
                    "as legally verified cadastral boundaries."
                )
            if self.cadastral_verification_evidence is None:
                raise ContractComplianceViolationError(
                    "Rule 2 Evidence Violation: `is_legally_verified_cadastre=True` requires a "
                    "validated `CadastralVerificationEvidence` record (survey plan reference, "
                    "issuing authority, SHA-256 document hash, surveyor license ID, and timestamp)."
                )
            try:
                self.cadastral_verification_evidence.validate()
            except ValueError as exc:
                raise ContractComplianceViolationError(f"Rule 2 Evidence Invalid: {exc}") from exc

        if self.projected_utm_epsg not in SUPPORTED_KENYA_METRIC_EPSG_CODES:
            raise ContractComplianceViolationError(
                f"Rule 6/7 Violation: Invalid or unprojected EPSG '{self.projected_utm_epsg}' "
                f"for Kenya metric analysis. Supported: {SUPPORTED_KENYA_METRIC_EPSG_CODES}"
            )
        KenyaCRSEngine.assert_projected_crs(self.projected_utm_epsg, require_kenya_epsg=True)

        if len(self.spatial_feature_vector.feature_uncertainties_1sigma) != len(
            self.spatial_feature_vector.feature_values
        ):
            raise ContractComplianceViolationError(
                "Rule 8 Violation: Every spatial feature must carry a corresponding 1-sigma uncertainty slot."
            )
        return True

    def to_json(self) -> str:
        self.validate_contract_invariants()
        payload_dict = {
            "contract_version": self.contract_version,
            "event_id": self.event_id,
            "timestamp_iso": self.timestamp_iso,
            "tenant_org_id": self.tenant_org_id,
            "property_id": self.property_id,
            "parcel_id": self.parcel_id,
            "wgs84_geojson_geometry": self.wgs84_geojson_geometry,
            "projected_utm_epsg": self.projected_utm_epsg,
            "spatial_keys": {
                "glitters_hex_res9": self.glitters_hex_res9,
                "glitters_hilbert_lvl16": self.glitters_hilbert_lvl16,
                "geohash_8": self.geohash_8,
                "grid_standards": {
                    "hex_grid": "GLITTERS_AXIAL_HEX_GRID_V1",
                    "hilbert_grid": "GLITTERS_PLANAR_HILBERT_V1",
                    "geohash": "IETF_BASE32_GEOHASH",
                },
            },
            "observation_authority": self.observation_authority,
            "is_legally_verified_cadastre": self.is_legally_verified_cadastre,
            "cadastral_verification_evidence": (
                {
                    "survey_plan_reference": self.cadastral_verification_evidence.survey_plan_reference,
                    "issuing_authority": self.cadastral_verification_evidence.issuing_authority,
                    "authenticated_document_sha256": self.cadastral_verification_evidence.authenticated_document_sha256,
                    "licensed_surveyor_id": self.cadastral_verification_evidence.licensed_surveyor_id,
                    "verification_timestamp_iso": self.cadastral_verification_evidence.verification_timestamp_iso,
                    "verification_status": self.cadastral_verification_evidence.verification_status,
                    "source_system_reference": self.cadastral_verification_evidence.source_system_reference,
                    "evidence_digest_sha256": self.cadastral_verification_evidence.compute_evidence_digest(),
                }
                if self.cadastral_verification_evidence is not None
                else None
            ),
            "inferred_legal_owner": self.inferred_legal_owner,
            "spatial_feature_vector": self.spatial_feature_vector.to_dict(),
            "detected_changes": list(self.detected_changes),
            "topological_warnings": list(self.topological_warnings),
        }
        return json.dumps(payload_dict, indent=2)
