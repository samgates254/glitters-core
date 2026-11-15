# ADR 0006 — Redis is not the system of record

- Status: accepted
- Date: 2026-11-10

## Context

Redis is required for cache, Celery, and a future channel layer. It can lose data. Financial state and booking occupancy cannot.

## Decision

PostgreSQL is authoritative for users, money, inventory, and bookings. Redis may cache reads and carry queue messages. Cache keys are prefixed with `glitters` and separated by logical database index from the broker and the result backend.

Celery acknowledges tasks late, prefetches one message at a time, and accepts JSON only.

## Consequences

A flushed Redis must not change who owns a property or whether a payment settled. Cache invalidation rules will be written beside each cached query when caching starts. No business query is cached in this milestone.
