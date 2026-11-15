# ADR 0005 — Integer money in minor units

- Status: accepted, not implemented
- Date: 2026-11-10

## Context

GLITTERS will handle subscriptions, rent, commissions, and M-Pesa payments. Floating-point money drifts.

## Decision

Store money as integers in minor units. For KES, one shilling is 100 minor units. Persist the currency code with the amount. Keep payment attempts, confirmed transactions, allocations, refunds, settlements, and reconciliation entries as separate rows.

## Consequences

No money columns exist yet. When they are added, application code and tests must use integers. A provider callback is not a confirmed transaction until its signature and identifiers have been checked.
