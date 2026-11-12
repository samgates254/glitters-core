from __future__ import annotations

from .envelopes import (
    AbstentionReason,
    AlgorithmDecisionEnvelope,
    AlgorithmExecutionEnvelope,
    AlgorithmicExecutionStatus,
    AuthorizedCallerContext,
    ConfidenceTier,
    ContractVersion,
    DecisionExplanation,
    EngineAuditEvent,
    HumanApprovalRequirement,
    IdempotencyMetadata,
    OrganizationContext,
    ProvenanceMetadata,
    UncertaintyBounds,
)
from .evidence import (
    CadastralVerificationEvidence,
    PhysicalMeasurementEvidence,
    ProfessionalBoard,
    ProfessionalLicenseEvidence,
    SettledPaymentEvidence,
    TenantCreditEvidence,
    TitleDeedEvidence,
)
from .bridges import (
    AssetValuationRentalBridge,
    ConstructionProcurementFinancialBridge,
    GeospatialValuationFeatureVector,
    PortfolioInvestmentBridge,
    PredictiveMaintenanceHandoffBridge,
    ProjectMilestoneMoveInBridge,
    RecommendationCandidateHandoffBridge,
    RentalClearingAllocationBridge,
    TenantRiskHandoffBridge,
)
from . import schemas_v1

__version__ = "1.1.0"

__all__ = [
    # Envelopes and Governance (v1.0 & v1.1)
    "ContractVersion",
    "OrganizationContext",
    "ProvenanceMetadata",
    "IdempotencyMetadata",
    "HumanApprovalRequirement",
    "AbstentionReason",
    "AlgorithmExecutionEnvelope",
    "AlgorithmDecisionEnvelope",
    "AlgorithmicExecutionStatus",
    "AuthorizedCallerContext",
    "ConfidenceTier",
    "UncertaintyBounds",
    "DecisionExplanation",
    "EngineAuditEvent",
    # Evidence (v1.0 & v1.1)
    "ProfessionalBoard",
    "ProfessionalLicenseEvidence",
    "CadastralVerificationEvidence",
    "PhysicalMeasurementEvidence",
    "SettledPaymentEvidence",
    "TenantCreditEvidence",
    "TitleDeedEvidence",
    # Cross-Engine Bridges (v1.0 & v1.1)
    "GeospatialValuationFeatureVector",
    "AssetValuationRentalBridge",
    "RentalClearingAllocationBridge",
    "ProjectMilestoneMoveInBridge",
    "ConstructionProcurementFinancialBridge",
    "TenantRiskHandoffBridge",
    "PortfolioInvestmentBridge",
    "RecommendationCandidateHandoffBridge",
    "PredictiveMaintenanceHandoffBridge",
    # Submodules
    "schemas_v1",
]
