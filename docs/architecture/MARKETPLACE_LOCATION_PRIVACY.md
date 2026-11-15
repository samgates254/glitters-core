# Marketplace location privacy

Status: implemented in M5. Decision: [ADR — Marketplace location disclosure](decisions/ADR-marketplace-location-disclosure.md).

## Disclosure levels

| Level | Where it is used |
|---|---|
| Country, administrative area name, locality name | Public listing payloads |
| Exact coordinates | Authorized property detail only, unchanged from M4 |
| Approximate public pin | Not implemented |

Public listing JSON may include `country`, `administrative_area`, and `locality`. It does not include coordinates, latitude, longitude, street, building, unit, postal code, parties, mandate evidence, moderation notes, rejection reasons, the property id, the `GLT-` reference, distance, or `has_coordinates`.

## What is refused

Public collection and detail reject `near`, `bbox`, `lat`, `lon`, `latitude`, `longitude`, `radius_m`, `distance`, and `radius`. Administrative-area filtering walks the parent chain of areas, up to eight levels. It does not use `ST_Contains`. Sorts are publication time and asking price. There is no distance sort.

The public queryset defers `property_location.coordinates`. Tests assert that the quoted column `"property_location"."coordinates"` is absent and that the SQL has no `ST_DWithin`, `ST_Distance`, or `ST_Contains`. `coordinates_set_at` may still be selected with the location row. That timestamp is not a point. The street may be selected server-side and dropped by the serializer. The privacy test checks the JSON, not the absence of `street_line` from SQL.

Rounding the stored point is not an approximate pin. M5 does not emit a rounded coordinate.

## What the public query reads

The public API reads eligible published listings. It does not list properties. A draft, rejected, paused, withdrawn, expired, or completed listing is not discoverable through the marketplace collection or detail. An unpublished or archived property is not exposed through marketplace search. A stranger who asks for a publisher listing receives 404. A same-organization member without `property.publish` receives 403.

The M4 property API is unchanged. `has_coordinates` and 100 metre distance bands remain there for authorized callers. They are not a marketplace disclosure path. Fetching the property does not reveal the listing reference.

## Limits

An operator who logs request bodies can still capture a point a client sent to the property API. That is an operational logging rule. A public name for a small locality still narrows a search. It does not reveal the survey point. No public map tile in M5 is generated from the stored coordinate.
