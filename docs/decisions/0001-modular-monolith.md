# ADR 0001 — Modular monolith

- Status: accepted
- Date: 2026-11-10

## Context

GLITTERS needs clear domains and a path to scale. The platform does not yet have a workload that justifies operating many networked services.

## Decision

Build one Django deployable with explicit app boundaries, service functions, and database constraints. Extract a process later only when its load, compliance boundary, or release cadence is independent.

## Consequences

Cross-domain calls stay in-process and can use database transactions. Teams must resist importing another app's models from views. Operational complexity stays at one API, one database, one cache, and one worker fleet.
