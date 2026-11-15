# ADR 0003 — PostgreSQL 16 and PostGIS 3.5 for development

- Status: accepted
- Date: 2026-11-10

## Context

The host has PostgreSQL 18 without the PostGIS extension. `postgis/postgis:16-3.5` is already present on the machine and matches a current Django 5.2 database target. Ports 5432 and 5433 are already used. The workstation does have GDAL 3.12.2 and GEOS 3.14.1 as shared libraries.

## Decision

Run a dedicated PostGIS 16.x container for GLITTERS CORE and publish it on `127.0.0.1:5434`. Enable the `postgis` extension in the first core migration. Use the PostGIS Django backend only when GDAL is installed. Otherwise use the PostgreSQL backend and still verify `PostGIS_Version()` over SQL.

Redis is published on `127.0.0.1:6381` because 6379 and 6380 are already used.

## Consequences

Development spatial behaviour matches PostGIS 3.5 on PostgreSQL 16, not the host PostgreSQL 18 instance. Production may run a newer PostgreSQL once that version is tested with the same Django release. Where GDAL is installed, Django uses the PostGIS backend. Where it is not, the project keeps the PostgreSQL backend and reports `glitters.W001`.
