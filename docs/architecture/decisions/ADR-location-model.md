# ADR — Location model

- Status: accepted
- Date: 2026-11-11
- Milestone: M4
- Builds on: [ADR 0003 — PostgreSQL 16 and PostGIS](../../decisions/0003-postgresql-16-postgis.md)

This decision is accepted before the M4 migrations.

## Context

Kenya is the first operating market. Counties, sub-counties, and wards are the Kenyan administrative names. Uganda, Tanzania, and Rwanda do not use that same ladder. A location schema with a `county` column and a `sub_county` column would have to be redesigned for the next country.

A street address and a map pin answer different questions. The address can exist before anyone has surveyed a point. The point is sensitive: it can identify a home more precisely than the address the parties intended to show.

M1 already enables PostGIS. M4 is the first schema that stores geometry.

## Decision

`apps.locations` has no dependency on organizations or properties.

### Reference geography

`country` is keyed by ISO 3166-1 alpha-2. Alpha-3 and the English name are attributes. A country can be marked inactive. New addresses cannot use an inactive country.

`administrative_level` defines an ordered ladder per country: a depth starting at 1 and a code such as `county` or `district`. The code is data. It is not a PostgreSQL enum of Kenyan terms.

`administrative_area` is one node in that ladder. It belongs to a country, optionally to a parent area in the same country, and to the level whose depth is exactly one greater than the parent's. A root area is depth 1. The parent chain is how M4 expresses containment.

`locality` is a city, town, neighbourhood, or other named place inside a country. It may point at an administrative area. It is reference data. The public API can search localities and cannot invent them.

The first seed is the level ladders, not a full gazetteer:

| Country | Depths |
|---|---|
| Kenya (`KE`) | county, sub-county, ward |
| Uganda (`UG`) | region, district, county, sub-county |
| Tanzania (`TZ`) | region, district, ward |
| Rwanda (`RW`) | province, district, sector, cell |

No official boundary polygons are stored. Loading every county polygon is a later location-intelligence choice, and it is not required to associate a property with an area the user selected. Containment queries in M4 walk this parent chain. They do not call `ST_Contains` on a boundary.

### Addresses

`address` stores country, administrative area, locality, street, building name, unit label, and postal code. Those fields do not require a coordinate. The area's country must match the address country. When both a locality and an area are present, the locality's area must be the selected area or lie under it. A broken chain is rejected. Skipping a level or crossing countries is rejected.

Resolving an address checks that chain and returns it. It does not create a property. The property service persists an address row only for that property. Address rows are not shared, so one property's edit cannot change another.

### Coordinates

`property_location.coordinates` is a PostGIS geography point, SRID 4326, nullable, with a GiST index. Latitude and longitude are validated before save. A database check rejects a stored point whose geometry SRID is not 4326 or whose coordinates fall outside the WGS84 range.

Distance filters use `ST_DWithin` on that geography column, so the unit is metres and the planner can use the GiST index. Bounding-box filters use the indexed point and reject a box larger than two degrees on either side. M4 does not scan the table in Python to compute distance.

Nearby and bounding-box search run only on properties the caller can already access. They are not a public marketplace search.

### Coordinate privacy

Exact coordinates are stored for authorized operations. They are not a default API field.

- List, nearby, bounding-box, and other search responses use a serializer that has no coordinate field.
- The detail serializer includes the point only when the caller may manage the property: `property.update` on the managing organization, or a `manage` access grant. Members, viewers, and `view` grants do not receive it.
- The same detail serializer removes the point when that flag is off. There is no second detail shape that returns it.
- List distance is rounded to 100 metre bands so a distance list is not an exact pin.
- `property_event` metadata records `coordinates_updated` and never the latitude, longitude, or point. Audit cleaning drops those keys.
- Django admin for the location row does not display the point.
- Logs of these services do not include the point.

Future public listings may show an approximate location. M4 does not invent an offset, because a reversible offset is not privacy.

## Consequences

- Adding a country is seed data: a country row and its level ladder. It does not require a migration that adds columns.
- Kenya is the first seed, not a special case in the tables.
- Official boundary geometry, geocoding, and a public approximate pin wait for a later milestone.
- Locality and administrative-area editing is not a public API. Tests and later staff tools can insert reference rows. Users can still type a street.

## Invariants

A child area matches its parent's country and the next depth. An address does not point at an area or locality in another country. A point is SRID 4326 or null. Search never returns another person's inaccessible property, and it never returns the stored point.
