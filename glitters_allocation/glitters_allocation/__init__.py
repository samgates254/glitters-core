"""
GLITTERS Property Allocation & Optimization Engine
===================================================
A mathematically rigorous, deterministic, explainable, constraint-driven property
allocation and optimization engine for multi-tenant real estate operations.

Target Architecture: Python 3.14+ / PostgreSQL 16+ / Django Service Layer Compatible
Zero dependency on Django ORM internals.
"""

from .contracts import (
    AllocationBatchInput,
    AllocationBatchResult,
    AllocationDecision,
    AllocationExplanation,
    AllocationPolicy,
    AllocationStatus,
    ApplicantProfile,
    ConstraintViolation,
    ExistingAssignment,
    FairnessMetrics,
    InventorySnapshot,
    OrganizationQuota,
    PropertyCategory,
    PropertyUnit,
    SolverRegime,
)
from .engine import GlittersAllocationEngine
from .postgres_adapter import (
    POSTGRESQL_16_DDL_SCHEMA,
    AllocationApplicationServiceAdapter,
    AuthorizedCallerContext,
    ConcurrentInventoryLockError,
    PostgresSQLCursorBackend,
    SQLiteTransactionalBackend,
    deserialize_batch_result,
    serialize_batch_result,
)

__version__ = "1.1.0"
__all__ = [
    "POSTGRESQL_16_DDL_SCHEMA",
    "AllocationApplicationServiceAdapter",
    "AllocationBatchInput",
    "AllocationBatchResult",
    "AllocationDecision",
    "AllocationExplanation",
    "AllocationPolicy",
    "AllocationStatus",
    "ApplicantProfile",
    "AuthorizedCallerContext",
    "ConcurrentInventoryLockError",
    "ConstraintViolation",
    "ExistingAssignment",
    "FairnessMetrics",
    "GlittersAllocationEngine",
    "InventorySnapshot",
    "OrganizationQuota",
    "PostgresSQLCursorBackend",
    "PropertyCategory",
    "PropertyUnit",
    "SQLiteTransactionalBackend",
    "SolverRegime",
    "deserialize_batch_result",
    "serialize_batch_result",
]
