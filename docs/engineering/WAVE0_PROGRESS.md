# Wave 0 progress

Branch: `fix/wave0-security-and-integrity`  
Base: `origin/main` at `0a4ea39`  
Worktree: `/home/mr-robot/Projects/glitters-core-wave0`

This slice is security and integrity only. It does not finish tenancy
lifecycle services, God's Eye workstation, Nairobi or Kisumu coverage, or
later professional workspaces. It is not a production-readiness claim.

## What changed

- Spatial reads and writes for private projects, jobs, analyses, and digital
  twins require an authenticated member of the owning organization.
- Tileset and raster file responses resolve only regular files inside
  `SPATIAL_TILESET_ROOT` and `SPATIAL_RASTER_ROOT`. Stored paths are not
  returned by the digital-twin or raster serializers.
- `RasterProcessingJob` records the caller's organization.
- Organization membership no longer mints a DIRECT property-management grant.
  An explicit direct grant is required before a person can propose a
  management agreement.
- Deadlock during agreement proposal returns the existing conflict error.
- An occupancy-reserving lease cannot move back to draft through `save`,
  `QuerySet.update`, or `bulk_update`. Changing `property` on an executed
  lease is still rejected.
- Staff and superusers without confirmed MFA are logged out of `/admin/` and
  rejected on `/api/`. Health checks stay public.
- Production and staging refuse an empty or local email backend.
- `glitters-geospatial-engine==1.1.0` is loaded from
  `vendor/wheels/glitters_geospatial_engine-1.1.0-py3-none-any.whl`.
  `uv lock --check` passed.

## Tests

Command, from the worktree, using the primary checkout virtualenv:

`pytest -q --tb=line --maxfail=40`

Result on 2026-11-26: **492 passed**, 1 warning.

The warning is the existing Shapely deprecation in
`apps/spatial/services/change_detection.py:312`.

`manage.py makemigrations --check --dry-run` reported no migration drift.
The suite applies migrations to pytest's isolated test database. The
long-lived development database was not migrated.

## Still open

- `ruff check .` reports 890 errors. `ruff format --check .` would reformat
  95 files. The new path, scope, and throttle modules are clean. The
  undefined name `Tuple` in `apps/spatial/services/temporal.py` is unchanged
  from `origin/main`. Lint cleanup stays out of this security diff.
- CI can install the vendored wheel. The existing lint and format steps still
  fail until that separate cleanup lands.
- Draft PR #2 (contract hashing) is unchanged and unmerged.
- Tenancy HTTP routes remain unmounted.
- TOTP secrets are signed, not encrypted at rest.
- God's Eye Cesium workstation, lease lifecycle services, and later waves
  are not part of this commit.

The dirty checkouts at `/home/mr-robot/Projects/glitters-core` and
`/home/mr-robot/glitters-core-release` were not modified.
