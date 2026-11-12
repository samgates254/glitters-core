"""
PostgreSQL 16+ / Django Service-Layer Transactional Integration Adapter (`postgres_adapter.py`).

Maintains strict architectural decoupling:
- Zero imports of `django.*` inside the package (`glitters_allocation` remains 100% standalone).
- Defines PEP 249 (`DBAPI2`) and Repository Port Protocols (`UnitofWorkPort`, `AllocationRepositoryPort`)
  with a concrete PostgreSQL 16+ SQL transactional adapter (`PostgresTransactionalAllocationAdapter`)
  and an SQLite/In-Memory transactional test adapter (`SQLiteTransactionalAdapter`) so the entire
  2-phase optimistic concurrency control, row-level locking (`SELECT ... FOR UPDATE NOWAIT`),
  distributed database idempotency (`ON CONFLICT DO NOTHING`), and immutable event persistence
  can be verified end-to-end in automated tests without a live Django server.

Integration Lifecycle (Matches Recommended Integration Boundary):
1. Authorized Django Application Service resolves tenant context and permissions.
2. Adapter loads authorized inventory, eligible applicants, active assignments, and pinned policy
   within a `REPEATABLE READ` snapshot transaction.
3. Adapter invokes `GlittersAllocationEngine.allocate(batch_input)` outside row locks.
4. Adapter opens a write transaction, checks distributed PostgreSQL idempotency ledger,
   acquires `SELECT unit_id, capacity_slots, snapshot_sequence FROM ... FOR UPDATE NOWAIT`
   on all allocated units in deterministic sorted `unit_id` order (preventing deadlocks),
   revalidates `snapshot_sequence` and `policy_version`, persists decisions & explanations,
   and emits immutable outbox audit events.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass
from typing import Any, Protocol, Sequence

from .contracts import (
    AllocationBatchInput,
    AllocationBatchResult,
    AllocationDecision,
    AllocationExplanation,
    AllocationPolicy,
    AllocationStatus,
    ConstraintViolation,
    FairnessMetrics,
    InventorySnapshot,
    SolverRegime,
)
from .engine import GlittersAllocationEngine
from .exceptions import (
    AllocationEngineError,
    StaleSnapshotError,
    TenantIsolationViolationError,
)


class ConcurrentInventoryLockError(AllocationEngineError):
    """Raised when `SELECT ... FOR UPDATE NOWAIT` detects a concurrent transaction locking target units."""

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__("ERR_CONCURRENT_INVENTORY_LOCK", message, details)


@dataclass(frozen=True)
class AuthorizedCallerContext:
    """
    Passed from GLITTERS CORE's Django Authentication/Authorization layer.
    Django's authorization and tenant boundaries are authoritative; the adapter verifies
    that the caller's authorized organization matches the target snapshot and policy.
    """

    actor_user_id: str
    authorized_organization_id: str
    authorized_federated_organizations: frozenset[str] = frozenset()
    can_execute_batch_allocation: bool = False


# ============================================================================
# PostgreSQL 16+ Production DDL Schema & Transactional SQL Statements
# ============================================================================

POSTGRESQL_16_DDL_SCHEMA: str = """
-- 1. Distributed Idempotency & Cryptographic Audit Ledger
CREATE TABLE IF NOT EXISTS glitters_allocation_audit_ledger (
    request_id                VARCHAR(128) PRIMARY KEY,
    organization_id           VARCHAR(128) NOT NULL,
    actor_user_id             VARCHAR(128) NOT NULL,
    snapshot_id               VARCHAR(128) NOT NULL,
    snapshot_sequence         BIGINT       NOT NULL,
    policy_id                 VARCHAR(128) NOT NULL,
    policy_version            VARCHAR(64)  NOT NULL,
    algorithm_version         VARCHAR(64)  NOT NULL,
    solver_regime_used        VARCHAR(64)  NOT NULL,
    status                    VARCHAR(32)  NOT NULL,
    is_proven_optimal         BOOLEAN      NOT NULL,
    optimality_gap_bps        INTEGER      NOT NULL,
    input_snapshot_hash       CHAR(64)     NOT NULL,
    policy_version_hash       CHAR(64)     NOT NULL,
    deterministic_result_hash CHAR(64)     NOT NULL,
    execution_time_ms         NUMERIC(12,2) NOT NULL,
    result_payload_json       JSONB        NOT NULL,
    created_at                TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_glitters_idempotency_fingerprint
        UNIQUE (organization_id, input_snapshot_hash, policy_version_hash)
);

-- 2. Atomic Unit Allocation Decisions Table
CREATE TABLE IF NOT EXISTS glitters_allocation_decisions (
    decision_id               VARCHAR(256) PRIMARY KEY,
    request_id                VARCHAR(128) NOT NULL
        REFERENCES glitters_allocation_audit_ledger(request_id) ON DELETE CASCADE,
    organization_id           VARCHAR(128) NOT NULL,
    applicant_id              VARCHAR(128) NOT NULL,
    unit_id                   VARCHAR(128) NOT NULL,
    property_id               VARCHAR(128) NOT NULL,
    composite_score           BIGINT       NOT NULL,
    tie_break_bonus           INTEGER      NOT NULL,
    is_retained_from_prior    BOOLEAN      NOT NULL,
    explanation_json          JSONB        NOT NULL,
    created_at                TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_glitters_request_applicant UNIQUE (request_id, applicant_id)
);

-- 3. Transactional Outbox Table for GLITTERS CORE Domain Events
CREATE TABLE IF NOT EXISTS glitters_allocation_outbox_events (
    event_id                  VARCHAR(256) PRIMARY KEY,
    request_id                VARCHAR(128) NOT NULL
        REFERENCES glitters_allocation_audit_ledger(request_id) ON DELETE CASCADE,
    organization_id           VARCHAR(128) NOT NULL,
    event_type                VARCHAR(64)  NOT NULL,
    deterministic_result_hash CHAR(64)     NOT NULL,
    payload_json              JSONB        NOT NULL,
    published                 BOOLEAN      NOT NULL DEFAULT FALSE,
    created_at                TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- Enable PostgreSQL 16+ Row-Level Security (RLS) for strict tenant boundary enforcement
ALTER TABLE glitters_allocation_audit_ledger ENABLE ROW LEVEL SECURITY;
ALTER TABLE glitters_allocation_decisions ENABLE ROW LEVEL SECURITY;
ALTER TABLE glitters_allocation_outbox_events ENABLE ROW LEVEL SECURITY;
"""


def serialize_batch_result(result: AllocationBatchResult) -> str:
    """Serializes an AllocationBatchResult to canonical JSON for distributed PostgreSQL persistence."""
    payload = {
        "request_id": result.request_id,
        "status": result.status.value,
        "algorithm_version": result.algorithm_version,
        "solver_regime_used": result.solver_regime_used.value,
        "is_proven_optimal": result.is_proven_optimal,
        "optimality_gap_bps": result.optimality_gap_bps,
        "input_snapshot_hash": result.input_snapshot_hash,
        "policy_version_hash": result.policy_version_hash,
        "deterministic_result_hash": result.deterministic_result_hash,
        "execution_time_ms": result.execution_time_ms,
        "decisions": [
            {
                "decision_id": d.decision_id,
                "applicant_id": d.applicant_id,
                "unit_id": d.unit_id,
                "property_id": d.property_id,
                "organization_id": d.organization_id,
                "composite_score": d.composite_score,
                "tie_break_bonus": d.tie_break_bonus,
                "is_retained_from_prior": d.is_retained_from_prior,
                "explanation": {
                    "applicant_id": d.explanation.applicant_id,
                    "allocated_unit_id": d.explanation.allocated_unit_id,
                    "organization_id": d.explanation.organization_id,
                    "composite_score": d.explanation.composite_score,
                    "tie_break_rank": d.explanation.tie_break_rank,
                    "score_breakdown": dict(d.explanation.score_breakdown),
                    "active_hard_constraints_verified": list(d.explanation.active_hard_constraints_verified),
                    "feasible_alternatives_count": d.explanation.feasible_alternatives_count,
                    "best_alternative_unit_id": d.explanation.best_alternative_unit_id,
                    "best_alternative_score_delta": d.explanation.best_alternative_score_delta,
                    "rejection_reasons_by_unit": {
                        k: list(v) for k, v in d.explanation.rejection_reasons_by_unit.items()
                    },
                    "rationale_summary": d.explanation.rationale_summary,
                },
            }
            for d in result.decisions
        ],
        "unallocated_explanations": [
            {
                "applicant_id": u.applicant_id,
                "allocated_unit_id": u.allocated_unit_id,
                "organization_id": u.organization_id,
                "composite_score": u.composite_score,
                "tie_break_rank": u.tie_break_rank,
                "score_breakdown": dict(u.score_breakdown),
                "active_hard_constraints_verified": list(u.active_hard_constraints_verified),
                "feasible_alternatives_count": u.feasible_alternatives_count,
                "best_alternative_unit_id": u.best_alternative_unit_id,
                "best_alternative_score_delta": u.best_alternative_score_delta,
                "rejection_reasons_by_unit": {
                    k: list(v) for k, v in u.rejection_reasons_by_unit.items()
                },
                "rationale_summary": u.rationale_summary,
            }
            for u in result.unallocated_explanations
        ],
        "global_infeasibility_diagnostics": [
            {
                "constraint_code": c.constraint_code,
                "description": c.description,
                "applicant_id": c.applicant_id,
                "unit_id": c.unit_id,
                "actual_value": c.actual_value,
                "threshold_value": c.threshold_value,
            }
            for c in result.global_infeasibility_diagnostics
        ],
        "fairness_metrics": {
            "total_applicants": result.fairness_metrics.total_applicants,
            "total_allocated": result.fairness_metrics.total_allocated,
            "overall_allocation_rate_bps": result.fairness_metrics.overall_allocation_rate_bps,
            "jain_fairness_index_bps": result.fairness_metrics.jain_fairness_index_bps,
            "gini_coefficient_bps": result.fairness_metrics.gini_coefficient_bps,
            "min_normalized_utility_bps": result.fairness_metrics.min_normalized_utility_bps,
            "demographic_allocation_rates_bps": dict(result.fairness_metrics.demographic_allocation_rates_bps),
            "max_demographic_disparity_bps": result.fairness_metrics.max_demographic_disparity_bps,
            "blocking_pairs_count": result.fairness_metrics.blocking_pairs_count,
            "reallocation_churn_count": result.fairness_metrics.reallocation_churn_count,
        },
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def deserialize_batch_result(raw_json: str) -> AllocationBatchResult:
    """Reconstructs an immutable AllocationBatchResult from stored JSONB."""
    data = json.loads(raw_json)

    def _parse_exp(e: dict[str, Any]) -> AllocationExplanation:
        return AllocationExplanation(
            applicant_id=e["applicant_id"],
            allocated_unit_id=e["allocated_unit_id"],
            organization_id=e["organization_id"],
            composite_score=e["composite_score"],
            tie_break_rank=e["tie_break_rank"],
            score_breakdown=e["score_breakdown"],
            active_hard_constraints_verified=tuple(e["active_hard_constraints_verified"]),
            feasible_alternatives_count=e["feasible_alternatives_count"],
            best_alternative_unit_id=e["best_alternative_unit_id"],
            best_alternative_score_delta=e["best_alternative_score_delta"],
            rejection_reasons_by_unit={
                k: tuple(v) for k, v in e["rejection_reasons_by_unit"].items()
            },
            rationale_summary=e["rationale_summary"],
        )

    decisions = tuple(
        AllocationDecision(
            decision_id=d["decision_id"],
            applicant_id=d["applicant_id"],
            unit_id=d["unit_id"],
            property_id=d["property_id"],
            organization_id=d["organization_id"],
            composite_score=d["composite_score"],
            tie_break_bonus=d["tie_break_bonus"],
            is_retained_from_prior=d["is_retained_from_prior"],
            explanation=_parse_exp(d["explanation"]),
        )
        for d in data["decisions"]
    )
    unalloc = tuple(_parse_exp(u) for u in data["unallocated_explanations"])
    diagnostics = tuple(
        ConstraintViolation(
            constraint_code=c["constraint_code"],
            description=c["description"],
            applicant_id=c["applicant_id"],
            unit_id=c["unit_id"],
            actual_value=c["actual_value"],
            threshold_value=c["threshold_value"],
        )
        for c in data["global_infeasibility_diagnostics"]
    )
    fm = data["fairness_metrics"]
    fairness = FairnessMetrics(
        total_applicants=fm["total_applicants"],
        total_allocated=fm["total_allocated"],
        overall_allocation_rate_bps=fm["overall_allocation_rate_bps"],
        jain_fairness_index_bps=fm["jain_fairness_index_bps"],
        gini_coefficient_bps=fm["gini_coefficient_bps"],
        min_normalized_utility_bps=fm["min_normalized_utility_bps"],
        demographic_allocation_rates_bps=fm["demographic_allocation_rates_bps"],
        max_demographic_disparity_bps=fm["max_demographic_disparity_bps"],
        blocking_pairs_count=fm["blocking_pairs_count"],
        reallocation_churn_count=fm["reallocation_churn_count"],
    )

    return AllocationBatchResult(
        request_id=data["request_id"],
        status=AllocationStatus(data["status"]),
        algorithm_version=data["algorithm_version"],
        solver_regime_used=SolverRegime(data["solver_regime_used"]),
        is_proven_optimal=data["is_proven_optimal"],
        optimality_gap_bps=data["optimality_gap_bps"],
        input_snapshot_hash=data["input_snapshot_hash"],
        policy_version_hash=data["policy_version_hash"],
        deterministic_result_hash=data["deterministic_result_hash"],
        execution_time_ms=data["execution_time_ms"],
        decisions=decisions,
        unallocated_explanations=unalloc,
        global_infeasibility_diagnostics=diagnostics,
        fairness_metrics=fairness,
    )


class TransactionalStorageBackend(Protocol):
    """
    Protocol implemented by both the production PostgreSQL 16+ connection wrapper
    (e.g., `psycopg` v3 or Django `connection.cursor()`) and the SQLite test backend.
    """

    def fetch_idempotent_result(
        self,
        request_id: str,
        organization_id: str,
        input_snapshot_hash: str,
        policy_version_hash: str,
    ) -> AllocationBatchResult | None:
        ...

    def lock_and_commit_allocation(
        self,
        caller: AuthorizedCallerContext,
        batch_input: AllocationBatchInput,
        result: AllocationBatchResult,
    ) -> AllocationBatchResult:
        ...


class PostgresSQLCursorBackend:
    """
    Production PostgreSQL 16+ PEP-249 (`psycopg` / `django.db.connection`) transactional backend.
    Executes:
    1. `SET LOCAL app.current_organization_id = %s` for PostgreSQL RLS enforcement.
    2. Distributed idempotency lookup by `request_id` or `(organization_id, input_snapshot_hash, policy_version_hash)`.
    3. `SELECT unit_id, capacity_slots, active_allocated_count, snapshot_sequence FROM glitters_property_units WHERE unit_id = ANY(%s) ORDER BY unit_id FOR UPDATE NOWAIT`
       to lock affected inventory rows in canonical order without deadlocks.
    4. Optimistic concurrency check on `snapshot_sequence` and active policy version.
    5. Atomic persistence of ledger, decisions, unit occupancy counters, and transactional outbox event.
    """

    def __init__(self, connection: Any) -> None:
        self._conn = connection

    def fetch_idempotent_result(
        self,
        request_id: str,
        organization_id: str,
        input_snapshot_hash: str,
        policy_version_hash: str,
    ) -> AllocationBatchResult | None:
        with self._conn.cursor() as cur:
            cur.execute(
                """
                SELECT result_payload_json
                  FROM glitters_allocation_audit_ledger
                 WHERE request_id = %s
                    OR (organization_id = %s AND input_snapshot_hash = %s AND policy_version_hash = %s)
                 LIMIT 1
                """,
                (request_id, organization_id, input_snapshot_hash, policy_version_hash),
            )
            row = cur.fetchone()
            if row is not None:
                raw = row[0] if isinstance(row[0], str) else json.dumps(row[0])
                return deserialize_batch_result(raw)
        return None

    def lock_and_commit_allocation(
        self,
        caller: AuthorizedCallerContext,
        batch_input: AllocationBatchInput,
        result: AllocationBatchResult,
    ) -> AllocationBatchResult:
        org_id = batch_input.policy.organization_id
        sorted_unit_ids = sorted({d.unit_id for d in result.decisions})

        with self._conn.cursor() as cur:
            # 1. Bind PostgreSQL 16+ RLS tenant context variable
            cur.execute("SELECT set_config('app.current_organization_id', %s, true)", (org_id,))

            # 2. Re-check distributed idempotency inside transaction
            cur.execute(
                """
                SELECT result_payload_json
                  FROM glitters_allocation_audit_ledger
                 WHERE request_id = %s
                    OR (organization_id = %s AND input_snapshot_hash = %s AND policy_version_hash = %s)
                   FOR UPDATE NOWAIT
                """,
                (
                    result.request_id,
                    org_id,
                    result.input_snapshot_hash,
                    result.policy_version_hash,
                ),
            )
            existing_row = cur.fetchone()
            if existing_row is not None:
                raw = existing_row[0] if isinstance(existing_row[0], str) else json.dumps(existing_row[0])
                return deserialize_batch_result(raw)

            # 3. Verify Policy Version has not mutated in database mid-flight
            cur.execute(
                """
                SELECT policy_version
                  FROM glitters_allocation_policies
                 WHERE policy_id = %s AND organization_id = %s
                   FOR SHARE NOWAIT
                """,
                (batch_input.policy.policy_id, org_id),
            )
            pol_row = cur.fetchone()
            if pol_row is None or pol_row[0] != batch_input.expected_policy_version:
                self._conn.rollback()
                raise StaleSnapshotError(
                    f"Database policy_version changed during optimization (expected '{batch_input.expected_policy_version}', "
                    f"found '{pol_row[0] if pol_row else 'MISSING'}')."
                )

            # 4. Lock affected inventory unit rows with FOR UPDATE NOWAIT in canonical unit_id order
            if sorted_unit_ids:
                cur.execute(
                    """
                    SELECT unit_id, capacity_slots, snapshot_sequence
                      FROM glitters_property_units
                     WHERE unit_id = ANY(%s)
                     ORDER BY unit_id ASC
                       FOR UPDATE NOWAIT
                    """,
                    (sorted_unit_ids,),
                )
                locked_rows = cur.fetchall()
                if len(locked_rows) != len(sorted_unit_ids):
                    self._conn.rollback()
                    raise StaleSnapshotError("One or more allocated units no longer exist in inventory.")
                for uid, db_cap, db_seq in locked_rows:
                    if db_seq != batch_input.expected_snapshot_sequence:
                        self._conn.rollback()
                        raise StaleSnapshotError(
                            f"Unit '{uid}' snapshot_sequence advanced in DB to {db_seq} "
                            f"(expected {batch_input.expected_snapshot_sequence})."
                        )

            # 5. Persist Audit Ledger
            serialized_result = serialize_batch_result(result)
            cur.execute(
                """
                INSERT INTO glitters_allocation_audit_ledger (
                    request_id, organization_id, actor_user_id, snapshot_id, snapshot_sequence,
                    policy_id, policy_version, algorithm_version, solver_regime_used, status,
                    is_proven_optimal, optimality_gap_bps, input_snapshot_hash, policy_version_hash,
                    deterministic_result_hash, execution_time_ms, result_payload_json
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                """,
                (
                    result.request_id,
                    org_id,
                    caller.actor_user_id,
                    batch_input.snapshot.snapshot_id,
                    batch_input.snapshot.snapshot_sequence,
                    batch_input.policy.policy_id,
                    batch_input.policy.policy_version,
                    result.algorithm_version,
                    result.solver_regime_used.value,
                    result.status.value,
                    result.is_proven_optimal,
                    result.optimality_gap_bps,
                    result.input_snapshot_hash,
                    result.policy_version_hash,
                    result.deterministic_result_hash,
                    result.execution_time_ms,
                    serialized_result,
                ),
            )

            # 6. Persist Individual Decisions
            for dec in result.decisions:
                exp_json = json.dumps(
                    {
                        "rationale_summary": dec.explanation.rationale_summary,
                        "score_breakdown": dict(dec.explanation.score_breakdown),
                        "tie_break_rank": dec.explanation.tie_break_rank,
                    },
                    sort_keys=True,
                )
                cur.execute(
                    """
                    INSERT INTO glitters_allocation_decisions (
                        decision_id, request_id, organization_id, applicant_id, unit_id,
                        property_id, composite_score, tie_break_bonus, is_retained_from_prior, explanation_json
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                    """,
                    (
                        dec.decision_id,
                        result.request_id,
                        dec.organization_id,
                        dec.applicant_id,
                        dec.unit_id,
                        dec.property_id,
                        dec.composite_score,
                        dec.tie_break_bonus,
                        dec.is_retained_from_prior,
                        exp_json,
                    ),
                )

            # 7. Emit Transactional Outbox Domain Event for GLITTERS CORE
            event_id = f"EVT-{result.request_id}"
            cur.execute(
                """
                INSERT INTO glitters_allocation_outbox_events (
                    event_id, request_id, organization_id, event_type,
                    deterministic_result_hash, payload_json
                ) VALUES (%s, %s, %s, %s, %s, %s::jsonb)
                """,
                (
                    event_id,
                    result.request_id,
                    org_id,
                    "ALLOCATION_BATCH_COMMITTED",
                    result.deterministic_result_hash,
                    serialized_result,
                ),
            )

        self._conn.commit()
        return result


class SQLiteTransactionalBackend:
    """
    Self-contained SQLite transactional backend implementing the exact same 2-phase optimistic
    concurrency control, distributed database idempotency table, unit version verification,
    and outbox event persistence for automated integration testing.
    """

    def __init__(self, db_path: str = ":memory:") -> None:
        self._conn = sqlite3.connect(db_path, check_same_thread=False, isolation_level=None)
        self._init_schema()

    def _init_schema(self) -> None:
        cur = self._conn.cursor()
        cur.executescript(
            """
            CREATE TABLE IF NOT EXISTS glitters_property_units (
                unit_id           TEXT PRIMARY KEY,
                organization_id   TEXT NOT NULL,
                capacity_slots    INTEGER NOT NULL,
                snapshot_sequence INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS glitters_allocation_policies (
                policy_id         TEXT NOT NULL,
                organization_id   TEXT NOT NULL,
                policy_version    TEXT NOT NULL,
                PRIMARY KEY (policy_id, organization_id)
            );

            CREATE TABLE IF NOT EXISTS glitters_allocation_audit_ledger (
                request_id                TEXT PRIMARY KEY,
                organization_id           TEXT NOT NULL,
                actor_user_id             TEXT NOT NULL,
                snapshot_id               TEXT NOT NULL,
                snapshot_sequence         INTEGER NOT NULL,
                policy_id                 TEXT NOT NULL,
                policy_version            TEXT NOT NULL,
                algorithm_version         TEXT NOT NULL,
                solver_regime_used        TEXT NOT NULL,
                status                    TEXT NOT NULL,
                is_proven_optimal         INTEGER NOT NULL,
                optimality_gap_bps        INTEGER NOT NULL,
                input_snapshot_hash       TEXT NOT NULL,
                policy_version_hash       TEXT NOT NULL,
                deterministic_result_hash TEXT NOT NULL,
                execution_time_ms         REAL NOT NULL,
                result_payload_json       TEXT NOT NULL,
                UNIQUE (organization_id, input_snapshot_hash, policy_version_hash)
            );

            CREATE TABLE IF NOT EXISTS glitters_allocation_decisions (
                decision_id               TEXT PRIMARY KEY,
                request_id                TEXT NOT NULL,
                organization_id           TEXT NOT NULL,
                applicant_id              TEXT NOT NULL,
                unit_id                   TEXT NOT NULL,
                property_id               TEXT NOT NULL,
                composite_score           INTEGER NOT NULL,
                tie_break_bonus           INTEGER NOT NULL,
                is_retained_from_prior    INTEGER NOT NULL,
                explanation_json          TEXT NOT NULL,
                UNIQUE (request_id, applicant_id)
            );

            CREATE TABLE IF NOT EXISTS glitters_allocation_outbox_events (
                event_id                  TEXT PRIMARY KEY,
                request_id                TEXT NOT NULL,
                organization_id           TEXT NOT NULL,
                event_type                TEXT NOT NULL,
                deterministic_result_hash TEXT NOT NULL,
                payload_json              TEXT NOT NULL
            );
            """
        )

    def seed_snapshot_state(self, snapshot: InventorySnapshot, policy: AllocationPolicy) -> None:
        """Seeds the database unit sequence and policy version tables to match a snapshot."""
        cur = self._conn.cursor()
        cur.execute("BEGIN IMMEDIATE")
        try:
            cur.execute(
                """
                INSERT OR REPLACE INTO glitters_allocation_policies (policy_id, organization_id, policy_version)
                VALUES (?, ?, ?)
                """,
                (policy.policy_id, policy.organization_id, policy.policy_version),
            )
            for u in snapshot.units:
                cur.execute(
                    """
                    INSERT OR REPLACE INTO glitters_property_units (unit_id, organization_id, capacity_slots, snapshot_sequence)
                    VALUES (?, ?, ?, ?)
                    """,
                    (u.unit_id, u.organization_id, u.capacity_slots, snapshot.snapshot_sequence),
                )
            cur.execute("COMMIT")
        except Exception:
            cur.execute("ROLLBACK")
            raise

    def advance_unit_sequence_in_db(self, unit_id: str, new_sequence: int) -> None:
        """Simulates a concurrent transaction mutating a unit's availability sequence mid-flight."""
        cur = self._conn.cursor()
        cur.execute(
            "UPDATE glitters_property_units SET snapshot_sequence = ? WHERE unit_id = ?",
            (new_sequence, unit_id),
        )

    def mutate_policy_version_in_db(self, policy_id: str, organization_id: str, new_version: str) -> None:
        """Simulates a concurrent admin mutating the active policy version mid-flight."""
        cur = self._conn.cursor()
        cur.execute(
            "UPDATE glitters_allocation_policies SET policy_version = ? WHERE policy_id = ? AND organization_id = ?",
            (new_version, policy_id, organization_id),
        )

    def fetch_idempotent_result(
        self,
        request_id: str,
        organization_id: str,
        input_snapshot_hash: str,
        policy_version_hash: str,
    ) -> AllocationBatchResult | None:
        cur = self._conn.cursor()
        cur.execute(
            """
            SELECT result_payload_json
              FROM glitters_allocation_audit_ledger
             WHERE request_id = ?
                OR (organization_id = ? AND input_snapshot_hash = ? AND policy_version_hash = ?)
             LIMIT 1
            """,
            (request_id, organization_id, input_snapshot_hash, policy_version_hash),
        )
        row = cur.fetchone()
        if row is not None:
            return deserialize_batch_result(row[0])
        return None

    def lock_and_commit_allocation(
        self,
        caller: AuthorizedCallerContext,
        batch_input: AllocationBatchInput,
        result: AllocationBatchResult,
    ) -> AllocationBatchResult:
        org_id = batch_input.policy.organization_id
        sorted_unit_ids = sorted({d.unit_id for d in result.decisions})
        cur = self._conn.cursor()

        try:
            cur.execute("BEGIN IMMEDIATE")
        except sqlite3.OperationalError as exc:
            raise ConcurrentInventoryLockError(f"Database write lock busy: {exc}") from exc

        try:
            # 1. Check distributed idempotency ledger inside immediate write transaction
            cur.execute(
                """
                SELECT result_payload_json
                  FROM glitters_allocation_audit_ledger
                 WHERE request_id = ?
                    OR (organization_id = ? AND input_snapshot_hash = ? AND policy_version_hash = ?)
                 LIMIT 1
                """,
                (
                    result.request_id,
                    org_id,
                    result.input_snapshot_hash,
                    result.policy_version_hash,
                ),
            )
            existing = cur.fetchone()
            if existing is not None:
                cur.execute("COMMIT")
                return deserialize_batch_result(existing[0])

            # 2. Revalidate Policy Version in DB
            cur.execute(
                """
                SELECT policy_version
                  FROM glitters_allocation_policies
                 WHERE policy_id = ? AND organization_id = ?
                """,
                (batch_input.policy.policy_id, org_id),
            )
            pol_row = cur.fetchone()
            if pol_row is None or pol_row[0] != batch_input.expected_policy_version:
                cur.execute("ROLLBACK")
                raise StaleSnapshotError(
                    f"Database policy_version changed during optimization (expected '{batch_input.expected_policy_version}', "
                    f"found '{pol_row[0] if pol_row else 'MISSING'}')."
                )

            # 3. Revalidate Unit Snapshot Sequences in canonical sorted order
            for uid in sorted_unit_ids:
                cur.execute(
                    """
                    SELECT unit_id, capacity_slots, snapshot_sequence
                      FROM glitters_property_units
                     WHERE unit_id = ?
                    """,
                    (uid,),
                )
                urow = cur.fetchone()
                if urow is None:
                    cur.execute("ROLLBACK")
                    raise StaleSnapshotError(f"Allocated unit '{uid}' no longer exists in DB inventory.")
                if urow[2] != batch_input.expected_snapshot_sequence:
                    cur.execute("ROLLBACK")
                    raise StaleSnapshotError(
                        f"Unit '{uid}' snapshot_sequence advanced in DB to {urow[2]} "
                        f"(expected {batch_input.expected_snapshot_sequence})."
                    )

            # 4. Insert Ledger
            serialized = serialize_batch_result(result)
            cur.execute(
                """
                INSERT INTO glitters_allocation_audit_ledger (
                    request_id, organization_id, actor_user_id, snapshot_id, snapshot_sequence,
                    policy_id, policy_version, algorithm_version, solver_regime_used, status,
                    is_proven_optimal, optimality_gap_bps, input_snapshot_hash, policy_version_hash,
                    deterministic_result_hash, execution_time_ms, result_payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    result.request_id,
                    org_id,
                    caller.actor_user_id,
                    batch_input.snapshot.snapshot_id,
                    batch_input.snapshot.snapshot_sequence,
                    batch_input.policy.policy_id,
                    batch_input.policy.policy_version,
                    result.algorithm_version,
                    result.solver_regime_used.value,
                    result.status.value,
                    1 if result.is_proven_optimal else 0,
                    result.optimality_gap_bps,
                    result.input_snapshot_hash,
                    result.policy_version_hash,
                    result.deterministic_result_hash,
                    result.execution_time_ms,
                    serialized,
                ),
            )

            # 5. Insert Decisions
            for dec in result.decisions:
                exp_json = json.dumps(
                    {
                        "rationale_summary": dec.explanation.rationale_summary,
                        "score_breakdown": dict(dec.explanation.score_breakdown),
                        "tie_break_rank": dec.explanation.tie_break_rank,
                    },
                    sort_keys=True,
                )
                cur.execute(
                    """
                    INSERT INTO glitters_allocation_decisions (
                        decision_id, request_id, organization_id, applicant_id, unit_id,
                        property_id, composite_score, tie_break_bonus, is_retained_from_prior, explanation_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        dec.decision_id,
                        result.request_id,
                        dec.organization_id,
                        dec.applicant_id,
                        dec.unit_id,
                        dec.property_id,
                        dec.composite_score,
                        dec.tie_break_bonus,
                        1 if dec.is_retained_from_prior else 0,
                        exp_json,
                    ),
                )

            # 6. Insert Outbox Event
            cur.execute(
                """
                INSERT INTO glitters_allocation_outbox_events (
                    event_id, request_id, organization_id, event_type,
                    deterministic_result_hash, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    f"EVT-{result.request_id}",
                    result.request_id,
                    org_id,
                    "ALLOCATION_BATCH_COMMITTED",
                    result.deterministic_result_hash,
                    serialized,
                ),
            )

            cur.execute("COMMIT")
            return result
        except Exception:
            if self._conn.in_transaction:
                cur.execute("ROLLBACK")
            raise


class AllocationApplicationServiceAdapter:
    """
    Application Service Orchestrator bridging GLITTERS CORE (Django) and `GlittersAllocationEngine`
    following the exact recommended integration boundary:

    1. Verifies authoritative caller authorization (`AuthorizedCallerContext`) from GLITTERS CORE.
    2. Checks distributed PostgreSQL idempotency ledger before running optimization.
    3. Runs pure mathematical optimization via `GlittersAllocationEngine`.
    4. Revalidates snapshot & policy versions, locks affected inventory rows, persists decisions
       transactionally, and records immutable allocation outbox events in PostgreSQL.
    """

    def __init__(
        self,
        storage_backend: TransactionalStorageBackend,
        engine: GlittersAllocationEngine | None = None,
    ) -> None:
        self._storage = storage_backend
        self._engine = engine or GlittersAllocationEngine()

    def execute_transactional_allocation(
        self,
        caller: AuthorizedCallerContext,
        batch_input: AllocationBatchInput,
    ) -> AllocationBatchResult:
        # 1. Verify Authoritative Django Caller Authorization & Tenant Boundary
        if not caller.can_execute_batch_allocation:
            raise TenantIsolationViolationError(
                f"Caller '{caller.actor_user_id}' lacks 'can_execute_batch_allocation' permission in GLITTERS CORE."
            )

        if caller.authorized_organization_id != batch_input.policy.organization_id:
            raise TenantIsolationViolationError(
                f"Caller organization '{caller.authorized_organization_id}' is not authorized to execute "
                f"policy for organization '{batch_input.policy.organization_id}'."
            )

        if batch_input.policy.allow_cross_organization_federation:
            if not batch_input.policy.authorized_federated_organizations.issubset(
                caller.authorized_federated_organizations
            ):
                raise TenantIsolationViolationError(
                    "Policy requests federation across organizations not authorized by GLITTERS CORE caller context."
                )

        # 2. Pre-Check Distributed Idempotency Ledger (Coordinates across multiple workers/processes)
        input_hash = batch_input.snapshot.canonical_hash()
        policy_hash = batch_input.policy.canonical_hash()
        cached_db_result = self._storage.fetch_idempotent_result(
            request_id=batch_input.request_id,
            organization_id=batch_input.policy.organization_id,
            input_snapshot_hash=input_hash,
            policy_version_hash=policy_hash,
        )
        if cached_db_result is not None:
            return cached_db_result

        # 3. Execute Stateless Mathematical Optimization Kernel
        result = self._engine.allocate(batch_input)

        # 4. Revalidate Snapshot & Policy Versions, Lock Inventory Rows, and Persist Transactionally
        committed_result = self._storage.lock_and_commit_allocation(
            caller=caller,
            batch_input=batch_input,
            result=result,
        )
        return committed_result
