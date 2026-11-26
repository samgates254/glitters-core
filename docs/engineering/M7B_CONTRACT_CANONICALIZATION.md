# M7-B contract canonicalization

Branch: `feat/m7b-contract-hash`  
Base: `fix/wave0-security-and-integrity` (`00f126e`)  
This branch does not merge draft PR #2 and does not implement signing.

## Algorithm

`apps/tenancy/services/document.py` hashes sorted compact JSON
(`sort_keys=True`, separators `(",", ":")`, `ensure_ascii=False`,
`allow_nan=False`) as UTF-8 with SHA-256. The hex digest is stored.

This is not RFC 8785 / JCS. Draft PR #2 used the same JSON shape and the
same incorrect RFC 8785 label. The label is removed. `document_version`
and `management_agreement_id` are now part of the payload. PR #2 omitted
both.

The rendered-document digest is SHA-256 of the exact bytes the caller
supplies. This service does not generate a PDF or a PDF/A.

## Included

| Field | Source |
| --- | --- |
| `contract_version` | Schema constant `1`. Not the row's document version. |
| `document_version` | `Lease.document_version` |
| `management_agreement_id` | UUID string, or JSON null |
| `parties[].user_id` | Party user UUID |
| `parties[].party_role` | Party role |
| `parties[].has_signature_authority` | Boolean |
| `terms.lease_type` | Lease type |
| `terms.property_id` | Unit UUID |
| `terms.property_owner_id` | Direct managing owner UUID |
| `terms.managing_organization_id` | Organization UUID, or JSON null |
| `terms.rent_amount_minor` | Integer minor units |
| `terms.rent_currency` | Three-letter currency |
| `terms.deposit_amount_minor` | Integer minor units |
| `terms.service_charge_minor` | Integer minor units |
| `terms.payment_due_day` | 1–28 |
| `terms.grace_period_days` | 0–30 |
| `terms.special_covenants` | Covenant text |
| `terms.start_date` | ISO date |
| `terms.term_end_date` | ISO date, or JSON null |

Parties are sorted by `(party_role, user_id)` before encoding.

## Excluded

`effective_end_date`, `status`, `signature_deadline`, `signed_at`,
`activated_at`, `terminated_at`, `actual_termination_date`,
`actual_handover_date`, `created_by_id`, `created_at`, `updated_at`,
`contract_data_hash`, `rendered_document_hash`, `rendered_document_uri`,
`termination_reason`, `reference`, and `id`.

Changing occupancy interval, status, or signature timing must not change
the contract digest. The stored digests are not inputs to themselves.

Signing and execution stay off this branch until these digest tests pass.
