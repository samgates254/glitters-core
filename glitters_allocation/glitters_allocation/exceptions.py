"""
Exceptions and Invariant Violation Hierarchy for GLITTERS Allocation Engine.
"""

from __future__ import annotations
from typing import Any


class AllocationEngineError(Exception):
    """Base exception for all GLITTERS allocation engine failures."""

    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.message = message
        self.details = details or {}


class TenantIsolationViolationError(AllocationEngineError):
    """Raised when an input or cross-reference violates multi-tenant organization boundaries (Invariant 2)."""

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__("ERR_TENANT_ISOLATION_VIOLATION", message, details)


class StaleSnapshotError(AllocationEngineError):
    """Raised when the inventory snapshot or policy version is stale or mismatched."""

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__("ERR_STALE_SNAPSHOT", message, details)


class ContradictoryPolicyError(AllocationEngineError):
    """Raised when hard constraints or organization quotas are mathematically self-contradictory."""

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__("ERR_CONTRADICTORY_POLICY", message, details)


class MaliciousInputError(AllocationEngineError):
    """Raised when inputs contain NaN, Inf, negative capacities, or overflow attacks."""

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__("ERR_MALICIOUS_INPUT", message, details)


class InvariantCheckFailureError(AllocationEngineError):
    """Raised if post-solve verification detects any violation of the 10 Critical Invariants (Invariant 8)."""

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__("ERR_INVARIANT_CHECK_FAILURE", message, details)
