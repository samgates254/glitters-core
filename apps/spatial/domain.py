"""Spatial domain constants, enums, canonical identity generators, and validation rules."""

from __future__ import annotations

import re
import uuid

# Standard SRID constants
SRID_WGS84 = 4326
SRID_KENYA_UTM_37S = 32737
SRID_KENYA_UTM_37N = 32637
SRID_KENYA_UTM_36S = 32736
SRID_KENYA_UTM_36N = 32636

# Canonical URN prefix
URN_PREFIX = "urn:glitters:spatial"
CANONICAL_URN_PATTERN = re.compile(
    r"^urn:glitters:spatial:([a-z_]+):([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$"
)

# Domain Errors
INVALID_URN = "Invalid canonical spatial URN format."
INVALID_GEOMETRY = "Geometry is invalid or contains self-intersections."
INVALID_SRID = "Geometry must have SRID 4326."
COORDINATES_OUT_OF_BOUNDS = "Coordinates are outside valid planetary bounds [-180, 180], [-90, 90]."
COORDINATES_OUTSIDE_ENVELOPE = "Coordinates fall outside the authorized regional geographic envelope."
PROJECTION_FAILED = "Geometry projection transformation failed."
EMPTY_GEOMETRY = "Geometry cannot be empty."
DIMENSION_MISMATCH = "Geometry dimension mismatch."
LINEAGE_CYCLE_DETECTED = "Acyclic entity lineage violation: cycle detected in transformation graph."
CROSS_ORG_ACCESS_DENIED = "You do not have permission to access spatial assets belonging to another organization."
SURVEYOR_CREDENTIAL_REQUIRED = "Licensed surveyor credentials required to access restricted survey observations."
RECORD_QUARANTINED = "Record geometry could not be validated or repaired; stored in quarantine."

# Geographic Operational Envelopes
MOMBASA_ENVELOPE_MIN_LON = 39.400000
MOMBASA_ENVELOPE_MAX_LON = 39.950000
MOMBASA_ENVELOPE_MIN_LAT = -4.350000
MOMBASA_ENVELOPE_MAX_LAT = -3.700000


class ReconciliationDisposition:
    INSERTED = "INSERTED"
    UPDATED = "UPDATED"
    UNCHANGED = "UNCHANGED"
    QUARANTINED = "QUARANTINED"

    CHOICES = (
        (INSERTED, "Inserted (New Canonical Entity)"),
        (UPDATED, "Updated (Geometry / Attribute Delta Applied)"),
        (UNCHANGED, "Unchanged (Exact Match / Idempotent No-op)"),
        (QUARANTINED, "Quarantined (Rejected by Quality Gate)"),
    )
    ALL = {c[0] for c in CHOICES}


class EntityType:
    BUILDING = "building"
    LAND_PARCEL = "land_parcel"
    ROAD = "road"
    CONSTRUCTION_SITE = "construction_site"
    SURVEY_OBSERVATION = "survey_observation"
    INFRASTRUCTURE_ASSET = "infrastructure_asset"
    ADMINISTRATIVE_BOUNDARY = "administrative_boundary"
    TERRAIN_FEATURE = "terrain_feature"
    IMAGERY_OBSERVATION = "imagery_observation"

    CHOICES = (
        (BUILDING, "Building"),
        (LAND_PARCEL, "Land Parcel"),
        (ROAD, "Road"),
        (CONSTRUCTION_SITE, "Construction Site"),
        (SURVEY_OBSERVATION, "Survey Observation"),
        (INFRASTRUCTURE_ASSET, "Infrastructure Asset"),
        (ADMINISTRATIVE_BOUNDARY, "Administrative Boundary"),
        (TERRAIN_FEATURE, "Terrain Feature"),
        (IMAGERY_OBSERVATION, "Imagery Observation"),
    )
    ALL = {c[0] for c in CHOICES}


class LifecycleStatus:
    PLANNED = "PLANNED"
    ACTIVE = "ACTIVE"
    UNDER_CONSTRUCTION = "UNDER_CONSTRUCTION"
    SUPERSEDED = "SUPERSEDED"
    DEMOLISHED = "DEMOLISHED"
    RETIRED = "RETIRED"

    CHOICES = (
        (PLANNED, "Planned"),
        (ACTIVE, "Active"),
        (UNDER_CONSTRUCTION, "Under Construction"),
        (SUPERSEDED, "Superseded"),
        (DEMOLISHED, "Demolished"),
        (RETIRED, "Retired"),
    )
    ALL = {c[0] for c in CHOICES}


class LegalAuthorityLevel:
    STATUTORY_AUTHORITATIVE = "STATUTORY_AUTHORITATIVE"
    LICENSED_SURVEY = "LICENSED_SURVEY"
    OPEN_DATA = "OPEN_DATA"
    OBSERVATIONAL_IMAGERY = "OBSERVATIONAL_IMAGERY"
    FIELD_INSPECTION = "FIELD_INSPECTION"
    AI_INFERRED = "AI_INFERRED"
    ALGORITHMIC_ESTIMATE = "ALGORITHMIC_ESTIMATE"

    CHOICES = (
        (STATUTORY_AUTHORITATIVE, "Statutory Authoritative (Gov Registry / Deed Plan)"),
        (LICENSED_SURVEY, "Licensed Professional Survey (Registered Surveyor)"),
        (OPEN_DATA, "Open Data (OSM / Overture)"),
        (OBSERVATIONAL_IMAGERY, "Observational Imagery (Satellite / Drone)"),
        (FIELD_INSPECTION, "Ground Field Inspection (GLITTERS Field Agent)"),
        (AI_INFERRED, "AI / Machine Learning Interpretation"),
        (ALGORITHMIC_ESTIMATE, "Algorithmic / Model-Derived Estimate"),
    )
    ALL = {c[0] for c in CHOICES}


class VerticalDatum:
    EGM2008_ORTHOMETRIC = "EGM2008_ORTHOMETRIC"
    EGM96_ORTHOMETRIC = "EGM96_ORTHOMETRIC"
    WGS84_ELLIPSOIDAL = "WGS84_ELLIPSOIDAL"
    KILINDINI_CHART_DATUM = "KILINDINI_CHART_DATUM"

    CHOICES = (
        (EGM2008_ORTHOMETRIC, "EGM2008 Orthometric (Mean Sea Level)"),
        (EGM96_ORTHOMETRIC, "EGM96 Orthometric"),
        (WGS84_ELLIPSOIDAL, "WGS 84 Ellipsoidal (Raw GNSS)"),
        (KILINDINI_CHART_DATUM, "Kilindini Harbour Tide Gauge Datum"),
    )
    ALL = {c[0] for c in CHOICES}


class DatasetCategory:
    BASEMAP = "BASEMAP"
    BUILDINGS = "BUILDINGS"
    CADASTRE = "CADASTRE"
    INFRASTRUCTURE = "INFRASTRUCTURE"
    IMAGERY = "IMAGERY"

    CHOICES = (
        (BASEMAP, "Basemap"),
        (BUILDINGS, "Buildings"),
        (CADASTRE, "Cadastre & Land Parcels"),
        (INFRASTRUCTURE, "Infrastructure & Utilities"),
        (IMAGERY, "Aerial / Satellite Imagery"),
    )
    ALL = {c[0] for c in CHOICES}


class DatasetVersionStatus:
    REGISTERED = "REGISTERED"
    INGESTING = "INGESTING"
    VALIDATING = "VALIDATING"
    PUBLISHED = "PUBLISHED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"

    CHOICES = (
        (REGISTERED, "Registered"),
        (INGESTING, "Ingesting"),
        (VALIDATING, "Validating"),
        (PUBLISHED, "Published"),
        (FAILED, "Failed"),
        (CANCELLED, "Cancelled"),
    )
    ALL = {c[0] for c in CHOICES}


class DigitalTwinFormat:
    GLTF_BINARY = "GLTF_BINARY"
    OGC_3DTILES = "OGC_3DTILES"

    CHOICES = (
        (GLTF_BINARY, "glTF 2.0 Binary (.glb)"),
        (OGC_3DTILES, "OGC 3D Tiles 1.1"),
    )
    ALL = {c[0] for c in CHOICES}


class HeightSource:
    OBSERVED_HEIGHT = "OBSERVED_HEIGHT"
    SOURCE_REPORTED_HEIGHT = "SOURCE_REPORTED_HEIGHT"
    LEVEL_DERIVED_HEIGHT = "LEVEL_DERIVED_HEIGHT"
    MODEL_ESTIMATED_HEIGHT = "MODEL_ESTIMATED_HEIGHT"
    SURVEY_VERIFIED_HEIGHT = "SURVEY_VERIFIED_HEIGHT"
    UNKNOWN_HEIGHT = "UNKNOWN_HEIGHT"

    CHOICES = (
        (OBSERVED_HEIGHT, "Direct Sensor / LiDAR Observed Height"),
        (SOURCE_REPORTED_HEIGHT, "Source Attribute Explicit Height"),
        (LEVEL_DERIVED_HEIGHT, "Storey Level Derived Height"),
        (MODEL_ESTIMATED_HEIGHT, "Statistical Model Estimated Height"),
        (SURVEY_VERIFIED_HEIGHT, "Ground Surveyor Verified Height"),
        (UNKNOWN_HEIGHT, "Unknown / Placeholder Height"),
    )
    ALL = {c[0] for c in CHOICES}


class DigitalTwinStatus:
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"

    CHOICES = (
        (PENDING, "Pending"),
        (PROCESSING, "Processing"),
        (COMPLETED, "Completed"),
        (FAILED, "Failed"),
    )
    ALL = {c[0] for c in CHOICES}


class ImageryDatasetType:
    OPTICAL_MULTISPECTRAL = "OPTICAL_MULTISPECTRAL"
    DIGITAL_ELEVATION_MODEL = "DIGITAL_ELEVATION_MODEL"
    RADAR_SAR = "RADAR_SAR"
    DERIVED_ANALYTICAL = "DERIVED_ANALYTICAL"

    CHOICES = (
        (OPTICAL_MULTISPECTRAL, "Optical Multispectral Imagery"),
        (DIGITAL_ELEVATION_MODEL, "Digital Elevation Model (DEM/DTM/DSM)"),
        (RADAR_SAR, "Synthetic Aperture Radar (SAR)"),
        (DERIVED_ANALYTICAL, "Derived Analytical / Spectral Product"),
    )
    ALL = {c[0] for c in CHOICES}


class RasterAssetType:
    COG = "COG"
    GEOTIFF = "GEOTIFF"
    PNG = "PNG"
    JPEG = "JPEG"
    WEBP = "WEBP"

    CHOICES = (
        (COG, "Cloud-Optimized GeoTIFF (COG)"),
        (GEOTIFF, "Standard GeoTIFF"),
        (PNG, "Portable Network Graphics (PNG)"),
        (JPEG, "JPEG Raster"),
        (WEBP, "WebP Raster"),
    )
    ALL = {c[0] for c in CHOICES}


class RasterOperation:
    INGESTION = "INGESTION"
    COG_CONVERSION = "COG_CONVERSION"
    NDVI = "NDVI"
    NDWI = "NDWI"
    NDBI = "NDBI"
    HILLSHADE = "HILLSHADE"
    SLOPE_ASPECT = "SLOPE_ASPECT"
    TEMPORAL_CHANGE = "TEMPORAL_CHANGE"

    CHOICES = (
        (INGESTION, "Imagery Ingestion"),
        (COG_CONVERSION, "COG Tile Optimization"),
        (NDVI, "Normalized Difference Vegetation Index (NDVI)"),
        (NDWI, "Normalized Difference Water Index (NDWI)"),
        (NDBI, "Normalized Difference Built-up Index (NDBI)"),
        (HILLSHADE, "Analytical Hillshade Shading"),
        (SLOPE_ASPECT, "Horn Slope & Aspect Analysis"),
        (TEMPORAL_CHANGE, "Multi-Temporal Change Detection"),
    )
    ALL = {c[0] for c in CHOICES}


class RasterJobStatus:
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"

    CHOICES = (
        (PENDING, "Pending"),
        (PROCESSING, "Processing"),
        (COMPLETED, "Completed"),
        (FAILED, "Failed"),
    )
    ALL = {c[0] for c in CHOICES}


class DatasetFormat:
    POSTGIS_MVT = "POSTGIS_MVT"
    COG_RASTER = "COG_RASTER"
    THREE_D_TILES = "3D_TILES"
    PMTILES = "PMTILES"
    GEOJSON = "GEOJSON"

    CHOICES = (
        (POSTGIS_MVT, "PostGIS Mapbox Vector Tile (MVT)"),
        (COG_RASTER, "Cloud-Optimized GeoTIFF (COG)"),
        (THREE_D_TILES, "Cesium 3D Tiles 1.1"),
        (PMTILES, "PMTiles Archive"),
        (GEOJSON, "GeoJSON Feature Collection"),
    )
    ALL = {c[0] for c in CHOICES}


class LicenseType:
    ODBL = "ODbL"
    CDLA_PERMISSIVE = "CDLA_PERMISSIVE"
    PROPRIETARY_GOVERNMENT = "PROPRIETARY_GOVERNMENT"
    GLITTERS_IP = "GLITTERS_IP"

    CHOICES = (
        (ODBL, "Open Database License (ODbL 1.0)"),
        (CDLA_PERMISSIVE, "Community Data License Agreement (CDLA-Permissive-2.0)"),
        (PROPRIETARY_GOVERNMENT, "Proprietary Government Registry License"),
        (GLITTERS_IP, "GLITTERS Proprietary Intellectual Property"),
    )
    ALL = {c[0] for c in CHOICES}


class UpdateFrequency:
    STATIC = "STATIC"
    MONTHLY = "MONTHLY"
    STREAMING = "STREAMING"
    ANNUAL = "ANNUAL"

    CHOICES = (
        (STATIC, "Static Historical Release"),
        (MONTHLY, "Monthly Update Cycle"),
        (STREAMING, "Real-time Streaming"),
        (ANNUAL, "Annual Survey Cycle"),
    )
    ALL = {c[0] for c in CHOICES}


class LineageType:
    SUBDIVISION = "SUBDIVISION"
    AMALGAMATION = "AMALGAMATION"
    REDEVELOPMENT = "REDEVELOPMENT"
    CORRECTION = "CORRECTION"
    REPLACEMENT = "REPLACEMENT"

    CHOICES = (
        (SUBDIVISION, "Cadastral Subdivision"),
        (AMALGAMATION, "Parcel Amalgamation / Merger"),
        (REDEVELOPMENT, "Building Redevelopment"),
        (CORRECTION, "Survey Boundary Correction"),
        (REPLACEMENT, "Entity Replacement"),
    )
    ALL = {c[0] for c in CHOICES}


class PropertyParcelAssocType:
    FULL_OWNERSHIP = "FULL_OWNERSHIP"
    SECTIONAL_TITLE = "SECTIONAL_TITLE"
    LEASEHOLD = "LEASEHOLD"
    EASEMENT = "EASEMENT"

    CHOICES = (
        (FULL_OWNERSHIP, "Full Freehold / Headlease Ownership"),
        (SECTIONAL_TITLE, "Sectional Property Title (Unit Ownership)"),
        (LEASEHOLD, "Registered Commercial Sublease"),
        (EASEMENT, "Wayleave / Access Easement"),
    )
    ALL = {c[0] for c in CHOICES}


class PropertyBuildingAssocType:
    ENTIRE_BUILDING = "ENTIRE_BUILDING"
    SECTIONAL_FLOORS = "SECTIONAL_FLOORS"
    TENANT_OCCUPATION = "TENANT_OCCUPATION"

    CHOICES = (
        (ENTIRE_BUILDING, "Entire Building"),
        (SECTIONAL_FLOORS, "Sectional Floors / Office Suites"),
        (TENANT_OCCUPATION, "Tenant Physical Occupation"),
    )
    ALL = {c[0] for c in CHOICES}


class BuildingClassification:
    RESIDENTIAL = "RESIDENTIAL"
    COMMERCIAL = "COMMERCIAL"
    INDUSTRIAL = "INDUSTRIAL"
    MIXED_USE = "MIXED_USE"
    INSTITUTIONAL = "INSTITUTIONAL"
    CIVIC = "CIVIC"
    OTHER = "OTHER"

    CHOICES = (
        (RESIDENTIAL, "Residential"),
        (COMMERCIAL, "Commercial Office & Retail"),
        (INDUSTRIAL, "Industrial & Warehousing"),
        (MIXED_USE, "Mixed Use Commercial/Residential"),
        (INSTITUTIONAL, "Educational / Healthcare"),
        (CIVIC, "Government & Civic Building"),
        (OTHER, "Other Unclassified"),
    )
    ALL = {c[0] for c in CHOICES}


class RoadClass:
    HIGHWAY = "HIGHWAY"
    PRIMARY = "PRIMARY"
    SECONDARY = "SECONDARY"
    LOCAL = "LOCAL"
    SERVICE = "SERVICE"

    CHOICES = (
        (HIGHWAY, "National Highway (KeNHA)"),
        (PRIMARY, "Primary Urban Arterial (KURA)"),
        (SECONDARY, "Secondary Rural Collector (KeRRA)"),
        (LOCAL, "Local Access Road"),
        (SERVICE, "Service Alley / Way"),
    )
    ALL = {c[0] for c in CHOICES}


class RoadSurface:
    ASPHALT = "ASPHALT"
    PAVED = "PAVED"
    MURRAM = "MURRAM"
    EARTH = "EARTH"

    CHOICES = (
        (ASPHALT, "Asphalt / Bitumen"),
        (PAVED, "Paved Concrete Blocks"),
        (MURRAM, "Graded Murram / Gravel"),
        (EARTH, "Unimproved Earth Road"),
    )
    ALL = {c[0] for c in CHOICES}


class ConstructionPhase:
    EXCAVATION = "EXCAVATION"
    FOUNDATION = "FOUNDATION"
    SUPERSTRUCTURE = "SUPERSTRUCTURE"
    CLADDING = "CLADDING"
    FITOUT = "FITOUT"
    COMMISSIONING = "COMMISSIONING"
    COMPLETED = "COMPLETED"

    CHOICES = (
        (EXCAVATION, "Site Excavation"),
        (FOUNDATION, "Foundation & Substructure"),
        (SUPERSTRUCTURE, "Superstructure Framing"),
        (CLADDING, "Cladding & Facade"),
        (FITOUT, "Internal Fitout"),
        (COMMISSIONING, "Final Commissioning"),
        (COMPLETED, "Completed & Certified"),
    )
    ALL = {c[0] for c in CHOICES}


class BeaconType:
    IRON_PIN_CONCRETE = "IRON_PIN_CONCRETE"
    BRASS_PLAQUE = "BRASS_PLAQUE"
    CONCRETE_PILLAR = "CONCRETE_PILLAR"
    NAIL_IN_TAR = "NAIL_IN_TAR"
    VIRTUAL_GNSS = "VIRTUAL_GNSS"

    CHOICES = (
        (IRON_PIN_CONCRETE, "Iron Pin in Concrete (Survey of Kenya Beacon)"),
        (BRASS_PLAQUE, "Brass Plaque / Benchmark Monument"),
        (CONCRETE_PILLAR, "Geodetic Concrete Pillar"),
        (NAIL_IN_TAR, "Steel Nail in Tar Roadway"),
        (VIRTUAL_GNSS, "Virtual GNSS Reference Station Point"),
    )
    ALL = {c[0] for c in CHOICES}


class InfrastructureType:
    WATER_MAIN = "WATER_MAIN"
    STORM_DRAIN = "STORM_DRAIN"
    SEWER = "SEWER"
    GRID_CABLE = "GRID_CABLE"
    TRANSFORMER = "TRANSFORMER"
    FIBER_CONDUIT = "FIBER_CONDUIT"

    CHOICES = (
        (WATER_MAIN, "Potable Water Distribution Main"),
        (STORM_DRAIN, "Stormwater Drainage Canal"),
        (SEWER, "Sanitary Sewer Line"),
        (GRID_CABLE, "Electrical Power Distribution Cable"),
        (TRANSFORMER, "Electrical Substation / Transformer"),
        (FIBER_CONDUIT, "Telecommunications Fiber Optic Duct"),
    )
    ALL = {c[0] for c in CHOICES}


def build_canonical_urn(entity_type: str, entity_id: uuid.UUID | str) -> str:
    """Builds an immutable, opaque canonical spatial URN."""
    type_str = entity_type.lower().strip()
    id_str = str(entity_id).lower().strip()
    return f"{URN_PREFIX}:{type_str}:{id_str}"


def validate_canonical_urn(urn: str) -> bool:
    """Validates that a string is a well-formed canonical spatial URN."""
    if not urn or not isinstance(urn, str):
        return False
    match = CANONICAL_URN_PATTERN.match(urn)
    if not match:
        return False
    entity_type = match.group(1)
    return entity_type in EntityType.ALL


def parse_canonical_urn(urn: str) -> tuple[str, uuid.UUID]:
    """Parses a canonical URN into entity_type and UUID. Raises ValueError if invalid."""
    if not validate_canonical_urn(urn):
        raise ValueError(INVALID_URN)
    match = CANONICAL_URN_PATTERN.match(urn)
    assert match is not None
    return match.group(1), uuid.UUID(match.group(2))


def map_osm_building_classification(tags: dict) -> str:
    """
    Deterministically maps OpenStreetMap building tags into canonical BuildingClassification choices.
    """
    if not tags or not isinstance(tags, dict):
        return BuildingClassification.OTHER

    raw_building = str(tags.get("building", "")).lower().strip()
    raw_amenity = str(tags.get("amenity", "")).lower().strip()
    raw_shop = str(tags.get("shop", "")).lower().strip()
    raw_office = str(tags.get("office", "")).lower().strip()

    # Check institutional / civic amenities first
    if raw_amenity in {
        "school",
        "university",
        "college",
        "hospital",
        "clinic",
        "kindergarten",
    } or raw_building in {
        "school",
        "university",
        "college",
        "hospital",
        "clinic",
        "kindergarten",
    }:
        return BuildingClassification.INSTITUTIONAL

    if raw_amenity in {
        "police",
        "courthouse",
        "townhall",
        "community_centre",
        "post_office",
        "fire_station",
    } or raw_building in {"civic", "government", "public"}:
        return BuildingClassification.CIVIC

    # Check commercial
    if (
        raw_building in {"commercial", "retail", "office", "supermarket", "hotel", "bank", "kiosk", "store"}
        or raw_shop
        or raw_office
        or raw_amenity in {"bank", "restaurant", "cafe", "fast_food"}
    ):
        return BuildingClassification.COMMERCIAL

    # Check industrial
    if raw_building in {
        "industrial",
        "warehouse",
        "factory",
        "hangar",
        "storage_tank",
        "silo",
        "manufacture",
    }:
        return BuildingClassification.INDUSTRIAL

    # Check mixed use
    if raw_building in {"mixed_use", "commercial;residential", "retail;residential"}:
        return BuildingClassification.MIXED_USE

    # Check residential
    if raw_building in {
        "residential",
        "house",
        "apartments",
        "detached",
        "dormitory",
        "terrace",
        "bungalow",
        "cabin",
        "semi_detached",
    }:
        return BuildingClassification.RESIDENTIAL

    return BuildingClassification.OTHER


class ChangeCategory:
    BUILDING_APPEARANCE_CANDIDATE = "BUILDING_APPEARANCE_CANDIDATE"
    BUILDING_REMOVAL_CANDIDATE = "BUILDING_REMOVAL_CANDIDATE"
    CONSTRUCTION_ACTIVITY_CANDIDATE = "CONSTRUCTION_ACTIVITY_CANDIDATE"
    VEGETATION_GAIN = "VEGETATION_GAIN"
    VEGETATION_LOSS = "VEGETATION_LOSS"
    WATER_BODY_CHANGE = "WATER_BODY_CHANGE"
    BARE_SOIL_CHANGE = "BARE_SOIL_CHANGE"
    LAND_COVER_CHANGE = "LAND_COVER_CHANGE"
    ROAD_OR_INFRASTRUCTURE_CANDIDATE = "ROAD_OR_INFRASTRUCTURE_CANDIDATE"
    UNKNOWN_CHANGE = "UNKNOWN_CHANGE"

    CHOICES = (
        (BUILDING_APPEARANCE_CANDIDATE, "Potential Built-Up / Building Appearance Candidate"),
        (BUILDING_REMOVAL_CANDIDATE, "Potential Demolition / Structure Removal Candidate"),
        (CONSTRUCTION_ACTIVITY_CANDIDATE, "Potential Construction Activity Candidate"),
        (VEGETATION_GAIN, "Vegetation Gain / Canopy Expansion"),
        (VEGETATION_LOSS, "Vegetation Loss / Canopy Reduction"),
        (WATER_BODY_CHANGE, "Water Body Extent Change"),
        (BARE_SOIL_CHANGE, "Bare Soil / Earthworks Change"),
        (LAND_COVER_CHANGE, "General Land Cover Conversion"),
        (ROAD_OR_INFRASTRUCTURE_CANDIDATE, "Potential Linear Infrastructure / Road Candidate"),
        (UNKNOWN_CHANGE, "Unclassified Spectral Change"),
    )
    ALL = {c[0] for c in CHOICES}


class ChangeCandidateStatus:
    OBSERVED = "OBSERVED"
    POTENTIAL = "POTENTIAL"
    MODEL_INFERRED = "MODEL_INFERRED"
    HUMAN_REVIEWED = "HUMAN_REVIEWED"
    VERIFIED = "VERIFIED"
    REJECTED = "REJECTED"
    SUPERSEDED = "SUPERSEDED"

    CHOICES = (
        (OBSERVED, "Observed (Machine Spectral Observation)"),
        (POTENTIAL, "Potential (Candidate Flagged for Review)"),
        (MODEL_INFERRED, "Model Inferred (Algorithmic / Change Vector Inferred)"),
        (HUMAN_REVIEWED, "Human Reviewed (Analyst Inspected)"),
        (VERIFIED, "Verified (Confirmed Real-World Physical Event)"),
        (REJECTED, "Rejected (False Positive / Artifact)"),
        (SUPERSEDED, "Superseded (Replaced by Subsequent Epoch)"),
    )
    ALL = {c[0] for c in CHOICES}


class ChangeProjectStatus:
    ACTIVE = "ACTIVE"
    ARCHIVED = "ARCHIVED"
    COMPLETED = "COMPLETED"

    CHOICES = (
        (ACTIVE, "Active Monitoring"),
        (ARCHIVED, "Archived"),
        (COMPLETED, "Completed"),
    )
    ALL = {c[0] for c in CHOICES}


class ChangeRunStatus:
    PENDING = "PENDING"
    VALIDATING = "VALIDATING"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"

    CHOICES = (
        (PENDING, "Pending"),
        (VALIDATING, "Validating Temporal Acquisitions"),
        (PROCESSING, "Processing Spectral Differences & Vectorization"),
        (COMPLETED, "Completed"),
        (FAILED, "Failed"),
        (CANCELLED, "Cancelled"),
    )
    ALL = {c[0] for c in CHOICES}


class ReviewDecision:
    CONFIRM_OBSERVATION = "CONFIRM_OBSERVATION"
    CONFIRM_FIELD_VERIFICATION = "CONFIRM_FIELD_VERIFICATION"
    REJECT_CANDIDATE = "REJECT_CANDIDATE"
    MARK_INCONCLUSIVE = "MARK_INCONCLUSIVE"
    REQUEST_FIELD_INSPECTION = "REQUEST_FIELD_INSPECTION"

    CHOICES = (
        (CONFIRM_OBSERVATION, "Confirm observation as potential real-world physical change"),
        (CONFIRM_FIELD_VERIFICATION, "Field verified / ground truth authorized physical event"),
        (REJECT_CANDIDATE, "Reject as false positive, sensor noise, or ephemeral artifact"),
        (MARK_INCONCLUSIVE, "Mark as inconclusive; requires higher resolution or temporal data"),
        (REQUEST_FIELD_INSPECTION, "Request ground field inspection"),
    )
    ALL = {c[0] for c in CHOICES}


class BuildingAssociationType:
    OVERLAPS_EXISTING = "OVERLAPS_EXISTING"
    ADJACENT_EXISTING = "ADJACENT_EXISTING"
    ISOLATED_UNMAPPED = "ISOLATED_UNMAPPED"
    PREVIOUSLY_OBSERVED = "PREVIOUSLY_OBSERVED"
    UNASSOCIATED = "UNASSOCIATED"

    CHOICES = (
        (OVERLAPS_EXISTING, "Candidate overlaps existing canonical building"),
        (ADJACENT_EXISTING, "Candidate is adjacent to existing building (< 15m)"),
        (ISOLATED_UNMAPPED, "Candidate in unmapped area without nearby buildings (> 30m)"),
        (PREVIOUSLY_OBSERVED, "Candidate corresponds to previously observed footprint"),
        (UNASSOCIATED, "No confident spatial association"),
    )
    ALL = {c[0] for c in CHOICES}


class SpatialAnalysisType:
    LAND_USE_CLASSIFICATION = "LAND_USE_CLASSIFICATION"
    URBAN_GROWTH_ANALYSIS = "URBAN_GROWTH_ANALYSIS"
    SPATIAL_CLUSTERING = "SPATIAL_CLUSTERING"
    ACCESSIBILITY_ANALYSIS = "ACCESSIBILITY_ANALYSIS"
    ENVIRONMENTAL_EXPOSURE = "ENVIRONMENTAL_EXPOSURE"
    DEVELOPMENT_SUITABILITY = "DEVELOPMENT_SUITABILITY"
    SPATIAL_FORECASTING = "SPATIAL_FORECASTING"

    CHOICES = (
        (LAND_USE_CLASSIFICATION, "Land-Use Evidence Classification"),
        (URBAN_GROWTH_ANALYSIS, "Urban Development & Growth Pattern Analysis"),
        (SPATIAL_CLUSTERING, "Spatial Clustering & Hotspot Analysis (DBSCAN, KDE, Gi*)"),
        (ACCESSIBILITY_ANALYSIS, "Infrastructure & Network Accessibility Analysis"),
        (ENVIRONMENTAL_EXPOSURE, "Terrain, Hydrological & Physical Exposure Analysis"),
        (DEVELOPMENT_SUITABILITY, "Multi-Criteria Development Suitability Analysis"),
        (SPATIAL_FORECASTING, "Bounded Scenario Spatial Forecasting"),
    )
    ALL = {c[0] for c in CHOICES}


class SpatialAnalysisRunStatus:
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"

    CHOICES = (
        (PENDING, "Pending"),
        (RUNNING, "Running Analysis Pipeline"),
        (COMPLETED, "Completed"),
        (FAILED, "Failed"),
        (CANCELLED, "Cancelled"),
    )
    ALL = {c[0] for c in CHOICES}


class EvidenceLabel:
    OBSERVED = "OBSERVED"
    INFERRED = "INFERRED"
    ESTIMATED = "ESTIMATED"
    MODELED = "MODELED"
    VERIFIED = "VERIFIED"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"

    CHOICES = (
        (OBSERVED, "Observed Physical Reality (Direct Sensor or Cadastral Survey)"),
        (INFERRED, "Inferred Attribute (Classified from Evidence & Heuristics)"),
        (ESTIMATED, "Estimated Measurement (Calculated with Uncertainty Bounds)"),
        (MODELED, "Modeled Simulation (Derived from Theoretical/Physical Model)"),
        (VERIFIED, "Verified Ground Truth (Field-Inspected & Certified)"),
        (INSUFFICIENT_EVIDENCE, "Insufficient Evidence to Assert Classification"),
    )
    ALL = {c[0] for c in CHOICES}


class LandUseClass:
    RESIDENTIAL = "RESIDENTIAL"
    COMMERCIAL = "COMMERCIAL"
    INDUSTRIAL = "INDUSTRIAL"
    INSTITUTIONAL = "INSTITUTIONAL"
    CIVIC = "CIVIC"
    MIXED_USE = "MIXED_USE"
    AGRICULTURAL = "AGRICULTURAL"
    VEGETATION = "VEGETATION"
    WATER = "WATER"
    BARE_LAND = "BARE_LAND"
    TRANSPORTATION = "TRANSPORTATION"
    UNKNOWN = "UNKNOWN"

    CHOICES = (
        (RESIDENTIAL, "Residential"),
        (COMMERCIAL, "Commercial & Retail"),
        (INDUSTRIAL, "Industrial & Warehousing"),
        (INSTITUTIONAL, "Institutional (Schools, Universities, Hospitals)"),
        (CIVIC, "Civic & Government"),
        (MIXED_USE, "Mixed-Use Development"),
        (AGRICULTURAL, "Agricultural & Cropland"),
        (VEGETATION, "Natural Vegetation & Forest Canopy"),
        (WATER, "Water Bodies & Maritime Channels"),
        (BARE_LAND, "Bare Ground & Exposed Soil"),
        (TRANSPORTATION, "Transportation & Road Infrastructure"),
        (UNKNOWN, "Unknown / Insufficient Evidence"),
    )
    ALL = {c[0] for c in CHOICES}


class ForecastingStatus:
    SUCCESS = "SUCCESS"
    INSUFFICIENT_TEMPORAL_DATA = "INSUFFICIENT_TEMPORAL_DATA"
    EXCEEDS_PREDICTIVE_HORIZON = "EXCEEDS_PREDICTIVE_HORIZON"

    CHOICES = (
        (SUCCESS, "Forecasting Scenario Successfully Projected"),
        (INSUFFICIENT_TEMPORAL_DATA, "Insufficient Historical Temporal Observations (< 3 Epochs)"),
        (EXCEEDS_PREDICTIVE_HORIZON, "Requested Forecast Horizon Exceeds Valid Predictive Envelope"),
    )
    ALL = {c[0] for c in CHOICES}
