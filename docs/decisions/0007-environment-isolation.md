# ADR 0007 — Environment isolation and secrets

- Status: accepted
- Date: 2026-11-10

## Context

Development secrets and databases have a habit of reaching production through a copied environment file.

## Decision

- Development may read `.env`, and it will not override variables already set in the process.
- Staging and production ignore `.env`.
- Those two modules refuse debug mode, placeholder secrets, and wildcard hosts.
- HTTPS redirect, secure cookies, and HSTS belong to the deployed modules.
- The container image defaults to the production settings module.
- `.env` is never committed.

## Consequences

A production process must receive a complete environment from its platform. The localhost database password checked into `compose.yaml` is a development convenience and is banned from any shared environment.
