# Listing lifecycle

Status: implemented in M5. The service layer owns transitions. Views do not set status.

## States

`draft`, `pending_review`, `published`, `rejected`, `paused`, `withdrawn`, `expired`, `completed`.

Advertisers do not publish their own listings. `pending_review` to `published` is staff only, and staff must have confirmed MFA. Staff are not property administrators and cannot skip the mandate.

## Advertiser transitions

| From | To |
|---|---|
| `draft` | `pending_review`, `withdrawn` |
| `pending_review` | `withdrawn` |
| `published` | `paused`, `withdrawn`, `completed` |
| `rejected` | `draft` (revise), `withdrawn` |
| `paused` | `published` (resume), `withdrawn`, `completed` |
| `withdrawn`, `expired`, `completed` | none |

## Moderation transitions

| From | To |
|---|---|
| `pending_review` | `published`, `rejected` |
| `published` | `paused` (suspend) |
| `paused` with `moderation_hold` | `pending_review` (reinstate) |

Suspend of a published or paused listing sets `paused` and `moderation_hold`, with a private note. The advertiser cannot resume while the hold is set. Reinstate clears the hold and returns the listing to `pending_review`. It must be approved again. Completing a held listing is allowed and clears the hold because the status leaves `paused`. The hold may be true only while the status is `paused`.

## System transitions

| From | To |
|---|---|
| `published`, `paused` | `expired`, `withdrawn` |
| `pending_review` | `withdrawn` |

`withdrawn` here is mandate revocation or mandate expiry, not the advertiser withdraw command. The event metadata records `mandate_revoked` or `mandate_expired`.

## Publication eligibility

Submit, approve, and resume call the same eligibility check. The public queryset applies the same rules without writing. A draft may be created against an active mandate whose `valid_from` is still in the future. That draft cannot be submitted until the date arrives.

If `valid_until` is already past, the next advertiser transition persists mandate expiry in its own transaction, withdraws published, paused, and pending-review listings on that mandate, and then the requested transition fails with 409 because the listing is already withdrawn. Drafts stay drafts.

If a published or paused listing is past `expires_at`, pause, withdraw, complete, and resume persist `expired` and return 409 `This listing has expired.` Renew persists expiry and then creates a new draft. Public detail persists expiry and returns 404. The public collection hides the row and does not write. `POST .../expire/` before the time returns 409 `This listing has not reached its expiry time.`

## Renew

Renew is allowed for an `expired` listing whose mandate still covers it. It inserts a new draft with a new `LST-` reference and copies title, description, highlights, the current price, and media links. The old row stays expired. The new advertiser is the person who renews it. Assisted-description acceptance is copied only with the unchanged text. The response is 201.

## Terminal rows

Withdrawn, expired, and completed listings are not reopened. A correction after rejection returns the same row to draft. A new offer after a terminal state is a new listing, subject to the one-open-type rule.

## Timestamps

Creation, submission, publication, expiration, withdrawal, rejection, pause, and completion timestamps are set by the transition that introduces them. Reinstate clears publication and expiration timestamps. Updates set `updated_at`.
