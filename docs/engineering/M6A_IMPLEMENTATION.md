# M6-A implementation

Date: 2026-11-11. Branch: `feature/media-infrastructure`. Parent: `10b03cdd4837754139f8a8ad800cfcdb02af6ef2`. This work is not committed and was not pushed.

## What shipped

M6-A replaces the M5 accepted media reference with a server-controlled upload into a private bucket. Cloudflare R2 is the first provider. Provider calls stay in `apps.marketplace.storage`. Development and tests use an in-memory adapter. Staging and production default to `r2` and fail closed when a request needs the bucket and the credentials are missing. Settings import does not require those credentials.

The server chooses `properties/{property_uuid}/media/{media_uuid}/original`, returns a short-lived presigned PUT, and does not store that URL. `stored` is set only after the object exists, the byte length matches the declared size and the configured limit, and the bytes decode as JPEG, PNG, or WebP, or parse as a PDF. A completion body cannot set `upload_status`. Documents stay private. A public listing exposes a verified photograph only as `/api/v1/marketplace/listings/{reference}/media/{link_id}/`.

`PropertyMedia` keeps its listing links. New verification columns are nullable or blank so older rows migrate. `MediaUploadSession` records the provider, bucket, key, declared size, measured size, SHA-256, and the session timestamps. Listing status is unchanged by removal: the media row is rejected, listing links are deleted, and the object is deleted after commit.

Authorization is the existing property decision. Photographs also allow an active in-force mandate. Documents require update rights. Strangers receive 404. There is no second authorization system, no application Celery task, and no new broker. Expired sessions and unfinished object deletes are retried when the next upload starts.

`boto3` is the S3 client because R2 speaks that API. Pillow decodes JPEG, PNG, and WebP. PDF validation is structural and does not add a PDF library. Resolved versions audited with the suite: boto3 1.44.0, botocore 1.44.0, jmespath 1.1.0, s3transfer 0.19.3, and pillow 13.0.0.

The decision is in `docs/architecture/decisions/ADR-media-storage.md`. Routes are in `docs/api/V1.md`.

## Schema

`marketplace.0002_media_infrastructure` adds the upload session and the property-media verification fields. It rebuilds the listing-event action check so the new media events are valid. It does not edit `marketplace.0001_marketplace_foundations` or the properties migrations.

`python manage.py migrate marketplace` applied `marketplace.0002_media_infrastructure` to the development database. It was not faked and the database was not reset.

## API

Routes stay under `/api/v1/marketplace/`. Publisher writes remain CSRF-protected. The public photograph read is anonymous and only succeeds for a verified listing photograph on an eligible published listing.

- `POST /api/v1/marketplace/properties/{property_id}/media/upload-sessions/` returns 201 and a presigned PUT. A client storage URL or `upload_status` is rejected.
- `POST /api/v1/marketplace/media/upload-sessions/{session_id}/complete/` verifies the object. A second completion of a stored session returns the same row.
- `POST /api/v1/marketplace/media/upload-sessions/{session_id}/cancel/` cancels a `created` session. Cancelling again is idempotent.
- `GET /api/v1/marketplace/properties/{property_id}/media/` lists media the caller may see. The old client-URL create on this path is gone.
- `POST /api/v1/marketplace/properties/{property_id}/media/{media_id}/download/` returns `{url, expires_in}`.
- `POST /api/v1/marketplace/properties/{property_id}/media/{media_id}/remove/` rejects the row, detaches listing links, and deletes the object.
- `GET /api/v1/marketplace/listings/{reference}/media/{link_id}/` returns a short-lived signed GET for one public photograph.

Defaults are 10 MiB for images (`10_485_760`), 20 MiB for documents (`20_971_520`), 10 minutes for the PUT, 2 minutes for a signed GET, and 15 minutes for the session. The cleanup batch defaults to 25 and is capped at 50.

## Verification

Commands were run from `/home/mr-robot/Projects/glitters-core` with the project virtualenv on 2026-11-11. The suite mocks object storage and does not use production R2 credentials.

- `ruff check .` passed.
- `ruff format --check .` passed. 207 files were already formatted.
- `python manage.py makemigrations --check --dry-run` reported no changes.
- `python manage.py check` reported no issues.
- `python manage.py showmigrations marketplace` showed `0001_marketplace_foundations` and `0002_media_infrastructure` applied.
- `pytest --cov=apps --cov=config --cov-report=term-missing` reported **168 passed** in 60.66 seconds. Coverage of `apps` and `config` was **89%** (7510 statements, 855 missed).
- `uv run pip-audit --progress-spinner off` reported `No known vulnerabilities found`.

An earlier subset of the media, marketplace API, authorization, privacy, and settings tests reported 41 passed in 33.44 seconds. The full suite above includes those tests.

The M5 suite was 152 passed in 52.26 seconds at 89% coverage (6901 statements, 766 missed), at implementation commit `553ed398d88c53edac92de637b84abd9dd1c3b23`. Those tests still run. The historical M5 sentence that Cloudinary and S3 were not wired is left in the M5 record. This milestone closes that limitation for the current architecture.

M6-A tests cover unauthorized upload attempts, unverified email, mandate photographs and refused mandate documents, expired and cancelled sessions, invalid declared types, invalid signatures, a header-only PDF, a missing object, a size mismatch, a file over the limit, successful JPEG, PNG, WebP, and PDF uploads, duplicate completion after the object is gone, private document delivery, public listing visibility, opaque test URLs, media removal, and the existing marketplace regressions.

This workstation did not push the branch and did not run GitHub Actions.

## Limitations

- A production R2 signed GET contains the object key, including the property id, for a caller who was allowed to fetch that file. The listing payload does not. The memory adapter used in tests returns an opaque URL.
- PDF checks require a header, an object, and `%%EOF`. They do not prove a well-formed PDF.
- Verification runs in the completion request. `uploaded` and `verifying` are saved inside that transaction and commit as `completed` or roll back to `created`.
- Session expiry and orphan deletion run lazily when another upload starts.
- The memory backend is process-local. It is not durable storage.
- Rows with an empty `storage_provider` can remain `stored` without `verified_at`. Public delivery ignores them. The migration does not rewrite those rows.
- No repository file contains R2 credentials. `.env` is not committed.
- Payments, bookings, deposits, escrow, commissions, invoices, M-Pesa, and title verification are not implemented.
- M1–M5 limitations other than the unwired media reference still apply.
- Passing tests are not a production launch. Hosting, backups, a private bucket in the target environment, a penetration test, and a data-protection assessment are still open.

## Decision

IMPLEMENTED, NOT COMMITTED. Broader M6 work and payments remain out of scope.
