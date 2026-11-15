# ADR — Marketplace location disclosure

- Status: accepted
- Date: 2026-11-11
- Milestone: M5
- Builds on: [ADR — Location model](ADR-location-model.md), [ADR — Marketplace engine](ADR-marketplace-engine.md)

This decision is accepted before the M5 marketplace migration.

## Context

M4 stores an optional geography point on the property location and a private street address. Exact coordinates can identify a home. The property API already withholds the point unless the caller may update the property, and it rounds a list distance to 100 metre bands. Those controls are not enough for a public marketplace. A public serializer, a distance sort, a bounding box, or a rounded pin can leak the same point through a different route.

A useful public listing still needs a place name. Buyers and tenants search by country, county, and locality. That is a different disclosure from the survey point.

## Decision

### Public disclosure

The public listing payload may include:

- the country code
- the administrative area name
- the locality name

It does not include latitude, longitude, a geometry, a distance, `has_coordinates`, street, building, unit, postal code, or the private access instructions stored on the property.

There is no public approximate pin. Rounding the stored point, snapping it to a grid, or adding noise and returning the result is rejected. An approximate pin would need its own published locality anchor, and M4 does not seed one. Until that anchor exists, the public map position is absent.

### Queries

Public collection and detail read eligible listings. They do not read every property. Draft, rejected, paused, withdrawn, expired, completed, and listings on a non-active property are not in the public queryset.

Geospatial parameters are rejected with 400: `near`, `bbox`, `lat`, `lon`, `latitude`, `longitude`, `radius_m`, `distance`, and `radius`. The public query does not call `ST_DWithin`, `ST_Contains`, or a distance expression. Administrative-area filtering walks descendant ids in application code, to a depth of eight, and matches the address area id. The property location point is deferred and is not selected for the public representation.

Sorting is publication time or asking price. It is not distance.

### Other private fields

The public payload also omits moderation notes, rejection reasons, mandate evidence, party rows, the internal property reference, and the property id. Advertiser contact email is omitted. The advertiser label is the person's given name and family name, or the word "Advertiser" when both are empty. An organization contributes its display name only.

Publisher and moderation responses add operational ids for the caller who already manages the listing. They still do not add coordinates. Moderation notes are staff-only.

The M4 property API is unchanged. `has_coordinates` and 100 metre distance bands remain on that API for authorized callers. They are not a marketplace disclosure path. A draft listing is not made public by fetching the property.

### Limits

A locality name can still be precise when the locality is small. The administrative hierarchy is public reference data. This policy stops the marketplace from returning the stored point and from letting a client calculate it back from marketplace queries. It does not anonymize a person who chooses a very small locality, and it does not alter the property API.

## Consequences

- Public clients cannot sort or filter marketplace listings by proximity.
- Tests must show the forbidden keys are absent and that the public SQL does not contain `ST_DWithin`.
- A later approximate pin is a new decision with an independent published coordinate, not a change to this serializer.

## Not chosen

- Rounding `property_location.coordinates` for a public pin.
- Reusing the M4 nearby or bbox filters on `/api/v1/marketplace/listings/`.
- Returning the street to anyone who can see the listing.
