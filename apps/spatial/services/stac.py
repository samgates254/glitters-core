"""STAC (SpatioTemporal Asset Catalog) v1.0.0 Service & Serializer Engine.

Generates compliant STAC Collections, STAC Items, and handles spatial/temporal catalog search.
"""

from __future__ import annotations

import json
from typing import Any

from django.contrib.gis.geos import Polygon
from django.db.models import Q, QuerySet

from apps.spatial.models import (
    ImageryCollection,
    ImageryItem,
    RasterAsset,
)

STAC_VERSION = "1.0.0"
EO_EXTENSION = "https://stac-extensions.github.io/eo/v1.1.0/schema.json"
PROJ_EXTENSION = "https://stac-extensions.github.io/projection/v1.1.0/schema.json"


def serialize_stac_collection(collection: ImageryCollection, base_url: str = "") -> dict[str, Any]:
    """Serializes an ImageryCollection model instance into a standard STAC Collection."""
    extent_bbox = [39.4, -4.3, 39.9, -3.8]
    if collection.spatial_extent:
        extent_bbox = list(collection.spatial_extent.extent)

    start_iso = collection.time_start.isoformat() if collection.time_start else "2020-01-01T00:00:00Z"
    end_iso = collection.time_end.isoformat() if collection.time_end else None

    return {
        "stac_version": STAC_VERSION,
        "stac_extensions": [],
        "id": collection.collection_id,
        "type": "Collection",
        "title": collection.title,
        "description": collection.description or collection.title,
        "license": collection.license,
        "extent": {
            "spatial": {"bbox": [extent_bbox]},
            "temporal": {"interval": [[start_iso, end_iso]]},
        },
        "providers": [
            {
                "name": collection.provider,
                "roles": ["producer", "licensor"],
            }
        ],
        "links": [
            {
                "rel": "self",
                "href": f"{base_url}/api/v1/spatial/imagery/collections/{collection.collection_id}/",
                "type": "application/json",
            },
            {
                "rel": "items",
                "href": f"{base_url}/api/v1/spatial/imagery/collections/{collection.collection_id}/items/",
                "type": "application/geo+json",
            },
        ],
        "glitters:dataset_type": collection.dataset_type,
        "glitters:gsd_m": float(collection.gsd_m) if collection.gsd_m else None,
        "glitters:native_crs": collection.native_crs,
    }


def serialize_stac_item(item: ImageryItem, base_url: str = "") -> dict[str, Any]:
    """Serializes an ImageryItem model instance into a standard STAC Item (GeoJSON Feature)."""
    geom_dict = json.loads(item.footprint.geojson) if item.footprint else None
    bbox = list(item.footprint.extent) if item.footprint else [0.0, 0.0, 0.0, 0.0]

    # Map raster assets into STAC assets
    stac_assets: dict[str, Any] = {}
    for asset in item.assets.all():
        roles = []
        if asset.asset_key in {"visual", "tci"}:
            roles = ["visual"]
        elif asset.asset_key in {"ndvi", "ndwi", "ndbi"}:
            roles = ["data", "index"]
        elif asset.asset_key in {"dem", "dsm", "dtm", "hillshade"}:
            roles = ["data", "elevation"]
        else:
            roles = ["data"]

        stac_assets[asset.asset_key] = {
            "href": f"{base_url}/api/v1/spatial/rasters/{asset.id}/file/",
            "type": asset.mime_type,
            "title": f"{item.item_id} - {asset.asset_key.upper()}",
            "roles": roles,
            "file:size": asset.file_size_bytes,
            "glitters:tile_url": f"{base_url}/api/v1/spatial/rasters/{asset.id}/tiles/{{z}}/{{x}}/{{y}}",
            "glitters:asset_id": str(asset.id),
            "proj:epsg": int(asset.crs.replace("EPSG:", "")) if asset.crs.startswith("EPSG:") else 32737,
            "proj:shape": [asset.height_px, asset.width_px],
            "gsd": float(asset.gsd_x_m),
            "nodata": asset.nodata_value,
        }

    properties: dict[str, Any] = {
        "datetime": item.acquisition_datetime.isoformat(),
        "created": item.created_at.isoformat(),
        "updated": item.updated_at.isoformat(),
        "eo:cloud_cover": float(item.cloud_coverage_percentage) if item.cloud_coverage_percentage is not None else 0.0,
        "view:sun_azimuth": float(item.sun_azimuth_deg) if item.sun_azimuth_deg is not None else None,
        "view:sun_elevation": float(item.sun_elevation_deg) if item.sun_elevation_deg is not None else None,
        "proj:epsg": int(item.native_crs.replace("EPSG:", "")) if item.native_crs.startswith("EPSG:") else 32737,
        "canonical_urn": item.canonical_urn,
        "collection": item.collection.collection_id,
        "provider": item.collection.provider,
        "license": item.collection.license,
        "attribution": item.collection.attribution,
    }
    # Merge custom source stac properties
    if item.stac_properties:
        properties.update(item.stac_properties)

    return {
        "stac_version": STAC_VERSION,
        "stac_extensions": [EO_EXTENSION, PROJ_EXTENSION],
        "type": "Feature",
        "id": item.item_id,
        "geometry": geom_dict,
        "bbox": bbox,
        "properties": properties,
        "assets": stac_assets,
        "links": [
            {
                "rel": "self",
                "href": f"{base_url}/api/v1/spatial/imagery/items/{item.id}/",
                "type": "application/geo+json",
            },
            {
                "rel": "collection",
                "href": f"{base_url}/api/v1/spatial/imagery/collections/{item.collection.collection_id}/",
                "type": "application/json",
            },
        ],
    }


def search_imagery_items(
    bbox: list[float] | None = None,
    time_start=None,
    time_end=None,
    collection_ids: list[str] | None = None,
    max_cloud_cover: float | None = None,
    limit: int = 50,
) -> QuerySet[ImageryItem]:
    """Filters ImageryItem records based on STAC spatial, temporal, and metadata parameters."""
    qs = ImageryItem.objects.select_related("collection").prefetch_related("assets").all()

    if collection_ids:
        qs = qs.filter(collection__collection_id__in=collection_ids)

    if bbox and len(bbox) == 4:
        # bbox = [min_lon, min_lat, max_lon, max_lat]
        query_poly = Polygon.from_bbox(bbox)
        qs = qs.filter(footprint__intersects=query_poly)

    if time_start:
        qs = qs.filter(acquisition_datetime__gte=time_start)

    if time_end:
        qs = qs.filter(acquisition_datetime__lte=time_end)

    if max_cloud_cover is not None:
        qs = qs.filter(
            Q(cloud_coverage_percentage__lte=max_cloud_cover)
            | Q(cloud_coverage_percentage__isnull=True)
        )

    return qs.order_by("-acquisition_datetime")[:limit]
