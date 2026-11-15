# GLITTERS / GOD'S EYE — M4 ARCHITECTURE SPECIFICATION
## Deliverable 7: Natural Language Spatial Assistant Architecture

**Document Reference:** `docs/engineering/M4_SPATIAL_ASSISTANT_ARCHITECTURE.md`
**Classification:** Core Architectural Standard
**Milestone:** M4 — Advanced Spatial Intelligence & Digital Twin Ecosystem
**Target Systems:** GLITTERS Core (`apps.spatial.assistant`), LLM Orchestrator, GOD'S EYE Chat & Command Interface

---

## 1. Executive Summary & Core Principle

Spatial GIS software has historically suffered from high operational friction: analysts must master complex UI menus, spatial SQL expressions, and coordinate reference systems.

The GLITTERS Spatial Assistant is an embedded conversational intelligence agent within GOD'S EYE that translates complex natural language queries into deterministic, permission-checked PostGIS spatial executions, camera movements, and interactive workstation layers.

### The Anti-Hallucination Invariant:
> **The Language Model NEVER computes geometries, coordinates, areas, or distances.**
> The model acts solely as a semantic compiler and orchestrator. It parses user intent, extracts spatial entities, plans tool calls, executes parameterized SQL/API operations against PostGIS, and explains the ground-truth results. All spatial facts are grounded directly in database records.

```
Spatial Assistant Architecture:
┌────────────────────────────────────────────────────────┐
│ User Prompt in GOD'S EYE                               │
│ "Find commercial buildings in Nyali over 15m tall      │
│  within 100m of a secondary school"                    │
└───────────────────────────┬────────────────────────────┘
                            │ Viewport Context: BBOX, Active Layers, Selected Entity
                            ▼
┌────────────────────────────────────────────────────────┐
│ Context Assembler & Spatial Intent Compiler            │
│ - Semantic Slot Extraction (Classes, Locations, Dist)  │
│ - Spatial Reference Resolution ("it", "the third one") │
└───────────────────────────┬────────────────────────────┘
                            │
                            ▼
┌────────────────────────────────────────────────────────┐
│ Deterministic Tool Call Planner (LLM Orchestrator)     │
│ Target Tools: `spatial_query`, `filter_entities`, ...  │
└───────────────────────────┬────────────────────────────┘
                            │
                            ▼
┌────────────────────────────────────────────────────────┐
│ Permission & Multi-Tenant Guardrail Checker            │
│ - Verifies user tenant_id, RLS, and security tier      │
└───────────────────────────┬────────────────────────────┘
                            │ Validated
                            ▼
┌────────────────────────────────────────────────────────┐
│ PostGIS Execution Engine (apps/spatial/assistant/exec) │
│ - Parameterized SQL Execution via Read-Only Connection │
│ - Spatial Filters: ST_DWithin, ST_Intersects, ST_Area  │
└───────────────────────────┬────────────────────────────┘
                            │ Returns GeoJSON FeatureCollection (N = 8)
                            ▼
┌────────────────────────────────────────────────────────┐
│ Workstation Action Dispatcher                          │
│ 1. Move camera / Fly-to cluster center                 │
│ 2. Highlight matching 3D buildings in cyan viewport    │
│ 3. Generate concise analytical text response to user   │
└────────────────────────────────────────────────────────┘
```

---

## 2. Spatial Tool Registry Specification

The Assistant interacts with GLITTERS Core strictly through a typed JSON Schema tool registry.

### 2.1 Core Tool Definitions
```json
[
  {
    "name": "spatial_search_entities",
    "description": "Finds spatial entities (buildings, parcels, infrastructure) satisfying geographic, attribute, and temporal criteria.",
    "parameters": {
      "type": "object",
      "properties": {
        "entity_type": {
          "type": "string",
          "enum": ["building", "land_parcel", "road", "construction_site"]
        },
        "neighborhood_or_poi": {
          "type": "string",
          "description": "Named locality (e.g., 'Nyali', 'Old Town', 'Kilindini Port')"
        },
        "spatial_predicate": {
          "type": "string",
          "enum": ["WITHIN_DISTANCE", "INTERSECTS", "CONTAINED_IN", "VIEWPORT_ONLY"]
        },
        "reference_poi_type": {
          "type": "string",
          "description": "Type of target to measure distance from (e.g., 'school', 'hospital', 'riparian_reserve')"
        },
        "distance_meters": {
          "type": "number",
          "description": "Proximity buffer in meters"
        },
        "attribute_filters": {
          "type": "object",
          "properties": {
            "min_height_m": {"type": "number"},
            "max_height_m": {"type": "number"},
            "building_type": {"type": "string"},
            "min_area_sqm": {"type": "number"}
          }
        },
        "as_of_date": {
          "type": "string",
          "description": "ISO date for historical point-in-time queries"
        }
      },
      "required": ["entity_type"]
    }
  },
  {
    "name": "cadastral_parcel_lookup",
    "description": "Retrieves legal ownership, deed history, and zoning regulations for a parcel number or building coordinate.",
    "parameters": {
      "type": "object",
      "properties": {
        "parcel_number": {"type": "string"},
        "building_id": {"type": "string"},
        "latitude": {"type": "number"},
        "longitude": {"type": "number"}
      }
    }
  },
  {
    "name": "compute_spatial_metrics",
    "description": "Calculates geometric and regulatory metrics (FAR, GCR, setback compliance, compactness) for specified entity.",
    "parameters": {
      "type": "object",
      "properties": {
        "entity_id": {"type": "string"},
        "metrics": {
          "type": "array",
          "items": {
            "type": "string",
            "enum": ["gcr", "far", "setback_road", "setback_boundary", "compactness", "solar_potential"]
          }
        }
      },
      "required": ["entity_id", "metrics"]
    }
  },
  {
    "name": "workstation_control",
    "description": "Directs the GOD'S EYE UI viewport (camera navigation, layer toggling, time slider).",
    "parameters": {
      "type": "object",
      "properties": {
        "action": {
          "type": "string",
          "enum": ["FLY_TO", "HIGHLIGHT_ENTITY", "SET_TIME_SLIDER", "TOGGLE_LAYER", "SELECT_3D"]
        },
        "target_coordinates": {
          "type": "array",
          "items": {"type": "number"},
          "description": "[longitude, latitude, altitude_m]"
        },
        "entity_id": {"type": "string"},
        "layer_name": {"type": "string"}
      },
      "required": ["action"]
    }
  }
]
```

---

## 3. Conversational Memory & Spatial Reference Resolution

Human users frequently reference previously discussed geographic features using pronouns and relative spatial indices:
- *"Show me the tall building next to the lighthouse."*
- *"Zoom into it."* (Resolves `it` to `entity_id` of the selected lighthouse).
- *"Who owns that parcel?"* (Resolves `that parcel` via spatial intersection with the active building).
- *"Compare its height with the surrounding structures."*

### 3.1 Session State Frame Structure
The assistant retains a sliding-window conversational frame:
```json
{
  "session_id": "ses_018f3a9e-8800",
  "active_selection": {
    "entity_id": "018f3a9e-0000-7000-8000-000000000042",
    "canonical_urn": "urn:glitters:entity:building:ke:msa:osm:way:554210",
    "coordinates": [39.6842, -4.0531],
    "entity_type": "building"
  },
  "last_query_results": [
    "018f3a9e-0000-7000-8000-000000000042",
    "018f3a9e-0000-7000-8000-000000000088",
    "018f3a9e-0000-7000-8000-000000000104"
  ],
  "viewport_bounding_box": [39.65, -4.08, 39.72, -4.02],
  "active_time": "2026-11-14T00:00:00Z"
}
```

When a user asks *"What is the footprint area of the second one?"*, the resolver maps *"second one"* to `last_query_results[1]`, queries the database for its pre-calculated area, and responds with authoritative precision.

---

## 4. Multi-Tenant Security & Permission Enforcement

Natural language must not bypass security firewalls.

### 4.1 Query Plan Verification Interceptor
Prior to executing any compiled SQL query:
1. **Tenant ID Injection:** If querying `PropertyBuildingAssociation` or private financial records, the user's `tenant_id` is automatically injected into the `WHERE` clause:
   ```sql
   AND tenant_id = :authenticated_user_tenant_id
   ```
2. **Classification Filtering:** If the user holds `PUBLIC_ANALYST` role, features flagged `RESTRICTED_DEFENSE` or `RESTRICTED_PORT_SECURITY` (e.g. Kenya Navy Base or Port Security installations) are masked or omitted.
3. **Execution Safety:** All assistant queries execute against a dedicated **read-only database connection** with a strict statement timeout ($2500\text{ ms}$) and max row limit ($1,000\text{ records}$).

---

## 5. End-to-End Execution Trace Example

```
User: "Highlight any buildings encroaching into the Mama Ngina Drive road reserve."

1. Assistant Intent Analysis:
   - Target Entity: 'building'
   - Spatial Relation: 'encroaching' (intersection with buffer)
   - Target Feature: Road named 'Mama Ngina Drive'
   - Regulatory Buffer: 6.0 meters

2. Tool Invocation:
   spatial_search_entities(
     entity_type="building",
     spatial_predicate="INTERSECTS",
     reference_poi_type="road_reserve",
     attribute_filters={"road_name": "Mama Ngina Drive", "reserve_buffer_m": 6.0}
   )

3. Backend PostGIS Query Executed:
   SELECT b.id, e.canonical_urn, ST_AsGeoJSON(e.geometry)
   FROM spatial_building b
   JOIN spatial_entity e ON b.spatialentity_ptr_id = e.id
   JOIN spatial_road r ON r.name ILIKE '%Mama Ngina Drive%'
   WHERE ST_Intersects(e.geometry, ST_Buffer(r.geometry::geography, 6.0)::geometry);

4. Backend Response:
   Found 3 encroaching structures.

5. Workstation Dispatch:
   - Dispatch `HIGHLIGHT_ENTITY` with the 3 IDs (rendering pulsing orange outline).
   - Dispatch `FLY_TO` to center viewport over the 3 structures.
   - Text reply: "Identified 3 buildings encroaching into the 6-meter road reserve along Mama Ngina Drive. The structures have been highlighted in the 3D viewport for inspection."
```
