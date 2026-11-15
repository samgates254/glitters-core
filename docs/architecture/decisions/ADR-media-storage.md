# ADR — Media storage

- Status: accepted
- Date: 2026-11-11
- Milestone: M6-A
- Builds on: [ADR — Marketplace engine](ADR-marketplace-engine.md)

## Context

M5 let a client submit an `https` URL or a `media/{uuid}` key and a status. `stored` meant the reference was accepted. The service did not read the file, so a missing object, a mismatched size, or a mislabelled type could still be attached to a listing.

Public listing JSON must not contain the property id. A private bucket also cannot offer a permanent public URL. The signed URL for a real object includes the object key, so that URL cannot be embedded in the listing payload.

Celery is configured and has no application tasks. Upload verification for these file sizes does not need a new queue.

## Decision

Cloudflare R2 is the first object-storage provider. Provider calls stay in `apps.marketplace.storage`. The bucket is private. There is no public ACL, no client-supplied storage URL, and no server-side fetch of a URL the client names.

Development and the test suite use `MEDIA_STORAGE_BACKEND=memory`. Staging and production default to `r2`. Missing R2 settings raise `ImproperlyConfigured` when a request constructs the client, not while Django settings are imported.

The server chooses the key `properties/{property_uuid}/media/{media_uuid}/original`. It returns a presigned PUT whose signed headers are `Content-Type` and `Content-Length`. Presigned URLs are not columns and are removed from audit metadata.

An upload session is `created`, then moves under a row lock through `uploaded` and `verifying`, and commits as `completed`, `rejected`, `expired`, or `cancelled`. A successful check commits `completed` in that same request. `stored` is set only after the object exists, the byte length matches the declared size and the configured limit, and the bytes decode as JPEG, PNG, or WebP, or parse as a PDF with a header, an object, and `%%EOF`. The declared MIME type must match the bytes. A completion body cannot override that result. Completing an already stored session returns the same row and does not download the object again.

Images default to 10 MiB and documents to 20 MiB. The PUT URL lasts up to 10 minutes, a signed GET lasts 2 minutes, and the session lasts 15 minutes. Those values are settings.

Documents stay `private`. Public discovery and the public photograph GET serve only a verified listing photograph on an eligible published listing. The listing field `asset_reference` is `/api/v1/marketplace/listings/{reference}/media/{link_id}/`. A caller who is allowed to open that photograph then receives a short-lived signed GET. On R2 that signed URL contains the object key, including the property id. The memory adapter used in tests returns an opaque URL so the suite can assert the listing payload and the test URL do not.

Removal sets the media to `rejected`, deletes listing links, and deletes the object. It does not change the listing status. Public discovery already hides a listing that no longer has a verified photograph. Expired `created` sessions, and rejected or cancelled objects whose delete did not finish, are retried when another upload starts. There is no Celery task and no second broker.

`boto3` is the S3 client because R2 speaks that API. A hand-rolled signature would be easier to mismatch. Pillow decodes the three image types. PDF validation does not add a PDF library.

Rows created before this milestone may have an empty `storage_provider`. The database still allows those rows to be `stored` without `verified_at`. New rows set the provider, and public delivery ignores a key that is not a verified provider object. The migration does not rewrite or delete existing media.

## Consequences

Authorization is unchanged: property update rights, or an active in-force mandate for photographs. Documents still require update rights. Strangers receive 404. Publisher writes stay CSRF-protected.

Payments, bookings, deposits, escrow, commissions, invoices, M-Pesa, and title verification are not part of this storage path.

A production signed GET reveals the object key to someone who was allowed to fetch that file. The listing payload does not.
