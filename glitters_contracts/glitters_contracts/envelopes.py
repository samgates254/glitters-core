from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Generic, Mapping, Optional, Sequence, TypeVar
from uuid import UUID
import hashlib
import json

TInput = TypeVar("TInput")
TOutput = TypeVar("TOutput")


class ContractVersion(str, Enum):
    V1_0 = "1.0.0"
    V1_1 = "1.1.0"


@dataclass(frozen=True, slots=True)
class OrganizationContext:
    """Multi-tenant organization boundary metadata managed by GLITTERS CORE."""
    tenant_org_id: UUID
    organization_name: str
    subscription_tier: str = "ENTERPRISE"
    is_active: bool = True
    authorized_features: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True, slots=True)
class AuthorizedCallerContext:
    """Security and execution context established by GLITTERS CORE before engine invocation."""
    caller_user_id: UUID
    tenant_org_id: UUID
    granted_permissions: frozenset[str]
    caller_ip_address: Optional[str] = None
    is_system_automated_run: bool = False
    org_context: Optional[OrganizationContext] = None

    def require_permission(self, permission: str) -> None:
        if permission not in self.granted_permissions:
            raise PermissionError(f"Caller lacking required algorithmic permission: '{permission}'")

    def has_permission(self, permission: str) -> bool:
        return permission in self.granted_permissions


@dataclass(frozen=True, slots=True)
class ProvenanceMetadata:
    """Complete provenance tracking ensuring reproducible execution context."""
    source_datasets: tuple[str, ...]
    dataset_sha256_hashes: tuple[str, ...]
    model_version: str
    model_weights_sha256: str
    is_synthetic_training: bool
    is_synthetic_inference: bool
    execution_environment: str = "SANDBOX_ISOLATED"
    git_commit_sha: str = "HEAD"


@dataclass(frozen=True, slots=True)
class IdempotencyMetadata:
    """Idempotency tracking ensuring deterministic and deduplicated execution."""
    idempotency_key: str
    created_at_utc: datetime
    input_fingerprint_sha256: str
    ttl_seconds: int = 86400


@dataclass(frozen=True, slots=True)
class HumanApprovalRequirement:
    """Explicit governance indicator for decisions requiring human/landlord sign-off."""
    requires_human_approval: bool
    approval_role: str = "AUTHORISED_REVIEWER"
    approval_reason: str = ""
    escalation_timeout_hours: int = 48


class AlgorithmicExecutionStatus(str, Enum):
    OPTIMAL = "OPTIMAL"
    FEASIBLE_APPROXIMATE = "FEASIBLE_APPROXIMATE"
    ABSTAINED_INSUFFICIENT_DATA = "ABSTAINED_INSUFFICIENT_DATA"
    OUT_OF_DISTRIBUTION = "OUT_OF_DISTRIBUTION"
    CONFLICTING_CONSTRAINTS = "CONFLICTING_CONSTRAINTS"
    INFEASIBLE = "INFEASIBLE"
    ABSTAINED_POLICY_VIOLATION = "ABSTAINED_POLICY_VIOLATION"


class AbstentionReason(str, Enum):
    NONE = "NONE"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
    OUT_OF_DISTRIBUTION = "OUT_OF_DISTRIBUTION"
    CONFLICTING_CONSTRAINTS = "CONFLICTING_CONSTRAINTS"
    UNVERIFIED_EVIDENCE = "UNVERIFIED_EVIDENCE"
    UNAUTHORIZED_TENANT_SCOPE = "UNAUTHORIZED_TENANT_SCOPE"
    SYNTHETIC_DATA_BLOCKED_IN_PRODUCTION = "SYNTHETIC_DATA_BLOCKED_IN_PRODUCTION"
    HARD_CONSTRAINT_VIOLATION = "HARD_CONSTRAINT_VIOLATION"
    SOLVER_TIMEOUT = "SOLVER_TIMEOUT"


class ConfidenceTier(str, Enum):
    HIGH_CONFIDENCE = "HIGH_CONFIDENCE"
    MODERATE_CONFIDENCE = "MODERATE_CONFIDENCE"
    WEAKLY_SUPPORTED = "WEAKLY_SUPPORTED"
    INSUFFICIENT_DATA_SAFE_FALLBACK = "INSUFFICIENT_DATA_SAFE_FALLBACK"
    OUT_OF_DISTRIBUTION = "OUT_OF_DISTRIBUTION"


@dataclass(frozen=True, slots=True)
class UncertaintyBounds:
    """Formal parametric or conformal uncertainty characterization."""
    point_estimate: float | int
    lower_bound: float | int
    upper_bound: float | int
    confidence_level: float
    interval_type: str
    effective_sample_size: float
    epistemic_status: str


@dataclass(frozen=True, slots=True)
class DecisionExplanation:
    """Explainability, sensitivity, and fairness telemetry."""
    summary_narrative: str
    feature_attributions: Mapping[str, float] = field(default_factory=dict)
    active_constraints: Sequence[str] = field(default_factory=list)
    sensitivity_elasticities: Mapping[str, float] = field(default_factory=dict)
    fairness_audit_passed: bool = True
    fairness_metrics: Mapping[str, float] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class EngineAuditEvent:
    """Immutable audit record ready for GLITTERS CORE persistence."""
    event_id: UUID
    execution_id: UUID
    tenant_org_id: UUID
    timestamp_utc: datetime
    engine_id: str
    action_type: str
    result_hash_sha256: str
    provenance_hash_sha256: str
    requires_human_approval: bool


@dataclass(frozen=True, slots=True)
class AlgorithmExecutionEnvelope(Generic[TInput]):
    """Universal execution envelope wrapping every engine invocation."""
    execution_id: UUID
    tenant_org_id: UUID
    authorized_caller: AuthorizedCallerContext
    engine_id: str
    engine_version: str
    policy_id: str
    policy_version: int
    idempotency_key: str
    created_at_utc: datetime
    input_snapshot: TInput
    input_snapshot_sha256: str
    contract_version: ContractVersion = ContractVersion.V1_1
    provenance: Optional[ProvenanceMetadata] = None

    def __post_init__(self) -> None:
        if self.authorized_caller.tenant_org_id != self.tenant_org_id:
            raise ValueError(
                f"Cross-tenant execution attempt: Caller org {self.authorized_caller.tenant_org_id} "
                f"does not match envelope tenant_org_id {self.tenant_org_id}"
            )


@dataclass(frozen=True, slots=True)
class AlgorithmDecisionEnvelope(Generic[TOutput]):
    """Universal decision envelope returned by every engine invocation."""
    decision_id: UUID
    execution_id: UUID
    tenant_org_id: UUID
    engine_id: str
    engine_version: str
    status: AlgorithmicExecutionStatus
    confidence_tier: ConfidenceTier
    uncertainty: UncertaintyBounds
    explanation: DecisionExplanation
    payload: TOutput
    requires_human_approval: bool
    audit_event: EngineAuditEvent
    result_hash_sha256: str
    execution_duration_ms: float
    contract_version: ContractVersion = ContractVersion.V1_1
    abstention_reason: AbstentionReason = AbstentionReason.NONE
    human_approval_requirement: Optional[HumanApprovalRequirement] = None
