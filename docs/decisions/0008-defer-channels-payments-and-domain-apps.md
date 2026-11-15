# ADR 0008 — Defer Channels, payments, and empty domain apps

- Status: accepted
- Date: 2026-11-10

## Context

The target platform includes real-time messaging, M-Pesa, maps, and many business domains. Adding them as empty apps and unwired adapters would look complete without being testable.

## Decision

This milestone ships the foundation only: settings, health, OpenAPI, the user table, PostGIS enablement, Redis, Celery configuration, Compose, CI, and the architecture records. Django Channels, payment adapters, map adapters, and the remaining domain apps wait for their milestones.

ASGI is configured so Channels can be mounted later without a new process model.

## Consequences

The repository stays small enough to review. The next implementation milestone after this decision was identity authentication, not a tour of empty CRUD. M3 later added `apps.organizations`. M4 later added `apps.locations` and `apps.properties`. M5 later added `apps.marketplace` for listings, mandates, asking prices, and media references. Channels, payments, and the other deferred apps remain uncreated.
