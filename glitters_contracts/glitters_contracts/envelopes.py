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


@dataclass(frozen=True, slots=True)
class AuthorizedCallerContext:
    """Security and execution context established by GLITTERS CORE before engine invocation."""
    caller_user_id: UUID
    tenant_org_id: UUID
    granted_permissions: frozenset[str]
    caller_ip_address: Optional[str] = None
    is_system_automated_run: bool = False

    def require_permission(self, permission: str) -> None:
        if permission not in self.granted_permissions:
            raise PermissionError(f"Caller lacking required algorithmic permission: '{permission}'")


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

    def __post_init__(self) -> None:
        if self.authorized_caller.tenant_org_id != self.tenant_org_id:
            raise ValueError(
                f"Cross-tenant execution attempt: Caller org {self.authorized_caller.tenant_org_id} "
                f"does not match envelope tenant_org_id {self.tenant_org_id}"
            )


class AlgorithmicExecutionStatus(str, Enum):
    OPTIMAL = "OPTIMAL"
    FEASIBLE_APPROXIMATE = "FEASIBLE_APPROXIMATE"
    ABSTAINED_INSUFFICIENT_DATA = "ABSTAINED_INSUFFICIENT_DATA"
    OUT_OF_DISTRIBUTION = "OUT_OF_DISTRIBUTION"
    CONFLICTING_CONSTRAINTS = "CONFLICTING_CONSTRAINTS"
    INFEASIBLE = "INFEASIBLE"


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
