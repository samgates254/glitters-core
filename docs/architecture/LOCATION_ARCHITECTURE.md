# Location architecture

Status: implemented in M4. The decision is [ADR — Location model](decisions/ADR-location-model.md). `apps.locations` does not import organizations or properties.

## Reference geography

Kenya is the first operating market. The tables do not assume it is the only country.

| Table | Role |
|---|---|
| `country` | ISO 3166-1 alpha-2 primary key, alpha-3, English name, active flag. |
| `administrative_level` | One rung of a country's ladder: depth starting at 1, and a code such as `county` or `district`. |
| `administrative_area` | One named node. Parent is optional. A child shares the parent's country and uses the next depth. |
| `locality` | City, town, or neighbourhood. It may point at an administrative area. |
| `address` | Structured postal fields. Independent of a coordinate. |

Containment is the parent chain. M4 does not store boundary polygons and does not call `ST_Contains`.

The seed migration inserts ladders, not a gazetteer of areas:

| Country | Depths |
|---|---|
| Kenya `KE` | county, sub-county, ward |
| Uganda `UG` | region, district, county, sub-county |
| Tanzania `TZ` | region, district, ward |
| Rwanda `RW` | province, district, sector, cell |

The public API browses and searches this reference data. It does not create countries, areas, or localities. A later staff tool can insert gazetteer rows. Users can still submit a street on a property.

## Addresses

Resolving an address validates the chain and returns it. It does not persist a row. The property service persists a private `address` for that property. Address rows are not shared, so editing one property cannot rewrite another.

Rules:

- An inactive or unknown country is rejected.
- The area's country matches the address country.
- The area's level belongs to that same country.
- A root area is depth 1. Each child is exactly one depth deeper than its parent.
- Skipping a level is invalid.
- When a locality has an area, that area must be the selected area or sit under it. If the request omits the area, the locality's area is used.
- A country plus a street, building, unit, or postal code is enough when no area is known. An active property still needs a country and a street, an area, or a locality.

## Coordinates

`property_location.coordinates` is a PostGIS geography point, SRID 4326, nullable. GeoDjango creates the GiST index from `spatial_index=True`. A `RunSQL` check rejects a stored point whose geometry SRID is not 4326 or whose longitude or latitude falls outside the WGS84 range.

The API accepts `{latitude, longitude}` only. The stored point is `(longitude, latitude)`. Booleans, non-finite numbers, and extra keys are rejected.

Nearby search uses `ST_DWithin` with a metre radius from 1 to 50000, on the caller's accessible properties. Bounding-box search uses an indexed intersection and rejects a box larger than two degrees on either side. Both filters may be combined. A nearby query orders by distance. M4 does not compute distance in Python over the whole table, and it does not implement marketplace search.

Administrative-area filters walk descendants with a recursive query on `administrative_area`, then filter address foreign keys. That is the parent chain, not a polygon.

Gazetteer name search uses `ILIKE` and a union of areas and localities. It is not a trigram index. Leading-wildcard search is acceptable for this small reference set. It is not used for property distance.

## Coordinate privacy

Exact coordinates are sensitive. Storage does not imply disclosure.

- List, nearby, and bounding-box serializers have no coordinate field.
- Detail includes the point only after authorization, when the caller has `property.update` or a `manage` grant. The same serializer drops the field otherwise. There is no alternate detail shape.
- A missing point is omitted rather than returned as null.
- Nearby distance is rounded to 100 metre bands. A distance of 0 stays 0.
- `has_coordinates` reports that a point exists. It does not reveal the point.
- Audit metadata stores `coordinates_updated` and drops latitude, longitude, coordinates, geometry, and secret keys.
- Django admin excludes the point from the location change form.
- Property and location services do not write the point to application logs.

Public listings disclose the country code, the administrative area name, and the locality name. They do not disclose the point, and they do not round it into an approximate pin. The rules are in [MARKETPLACE_LOCATION_PRIVACY.md](MARKETPLACE_LOCATION_PRIVACY.md). An operator proxy that records request bodies could still capture a point the client sent to the property API. That is an operational logging rule, not an API serializer.

## API

Routes are in [V1.md](../api/V1.md). Reads require an active account. Resolve is `POST` and still does not insert. Unsafe location views opt back into CSRF. Pagination matches the rest of v1.
