# ADR 0002 — Django 5.2 LTS on Python 3.14

- Status: accepted
- Date: 2026-11-10

## Context

The workstation has Python 3.14 only. Django 5.2 LTS supports Python 3.14 from 5.2.8 and receives security fixes until April 2028. Django 6.1 is the current feature release and its security support ends in December 2027.

## Decision

Pin Django to the 5.2 LTS line and Python to 3.14. Pin the resolved dependency set in `uv.lock`.

## Consequences

The project does not track Django 6 feature work. The planned upgrade is from 5.2 LTS to the following LTS before April 2028. This decision can be revisited before the first production release if a required fix exists only on Django 6.
