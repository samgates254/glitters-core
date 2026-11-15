"""OGC 3D Tiles 1.1 & glTF 2.0 Binary (GLB) Generation Engine.

Assembles extruded building meshes into streamable, interactive 3D tilesets
with embedded feature batch tables and geodetic coordinate transformations.
"""

from __future__ import annotations

import json
import math
import os
import struct
import time
import uuid
from pathlib import Path
from typing import Any

from django.conf import settings
from django.contrib.gis.geos import MultiPolygon, Polygon
from django.db import transaction

from apps.spatial.domain import (
    DigitalTwinFormat,
    DigitalTwinStatus,
    HeightSource,
    SRID_WGS84,
)
from apps.spatial.models import (
    Building,
    DigitalTwinAsset,
    DigitalTwinAttributeProvenance,
    DigitalTwinGeneration,
    SpatialSourceFeature,
)
from apps.spatial.services.twin_reconstruction import (
    MeshData,
    compute_enu_transform_matrix,
    extrude_building,
)

STORAGE_ROOT = Path(settings.MEDIA_ROOT) / "digital_twins" if hasattr(settings, "MEDIA_ROOT") and settings.MEDIA_ROOT else Path("/tmp/glitters_digital_twins")


def pack_mesh_to_glb(mesh: MeshData, batch_metadata: list[dict[str, Any]]) -> bytes:
    """
    Packs a MeshData structure and associated batch table metadata into
    a fully compliant glTF 2.0 Binary (.glb) asset.
    """
    num_indices = len(mesh.indices)
    num_vertices = len(mesh.positions) // 3

    if num_indices == 0 or num_vertices == 0:
        # Return empty valid glb
        empty_gltf = {
            "asset": {"version": "2.0", "generator": "GLITTERS 3D Digital Twin 1.0"},
            "scenes": [{"nodes": []}],
        }
        return _wrap_glb(empty_gltf, b"")

    # 1. Binary buffers
    # Indices: unsigned int (uint32, 4 bytes each)
    indices_bytes = struct.pack(f"<{num_indices}I", *mesh.indices)
    indices_pad = (4 - (len(indices_bytes) % 4)) % 4
    indices_bytes += b"\x00" * indices_pad

    # Positions: float32 (3 floats = 12 bytes each)
    positions_bytes = struct.pack(f"<{len(mesh.positions)}f", *mesh.positions)
    positions_pad = (4 - (len(positions_bytes) % 4)) % 4
    positions_bytes += b"\x00" * positions_pad

    # Normals: float32 (3 floats = 12 bytes each)
    normals_bytes = struct.pack(f"<{len(mesh.normals)}f", *mesh.normals)
    normals_pad = (4 - (len(normals_bytes) % 4)) % 4
    normals_bytes += b"\x00" * normals_pad

    # Batch IDs: float32 (1 float = 4 bytes each)
    batch_ids_bytes = struct.pack(f"<{len(mesh.batch_ids)}f", *mesh.batch_ids)
    batch_ids_pad = (4 - (len(batch_ids_bytes) % 4)) % 4
    batch_ids_bytes += b"\x00" * batch_ids_pad

    offset_indices = 0
    len_indices = len(indices_bytes)

    offset_positions = offset_indices + len_indices
    len_positions = len(positions_bytes)

    offset_normals = offset_positions + len_positions
    len_normals = len(normals_bytes)

    offset_batch_ids = offset_normals + len_normals
    len_batch_ids = len(batch_ids_bytes)

    total_bin_bytes = indices_bytes + positions_bytes + normals_bytes + batch_ids_bytes

    # 2. Build Batch Table
    batch_table: dict[str, list[Any]] = {
        "canonical_urn": [],
        "id": [],
        "osm_id": [],
        "name": [],
        "classification": [],
        "height_m": [],
        "height_source": [],
        "is_placeholder": [],
    }

    for item in batch_metadata:
        batch_table["canonical_urn"].append(str(item.get("canonical_urn", "")))
        batch_table["id"].append(str(item.get("canonical_urn", "")))
        batch_table["osm_id"].append(str(item.get("osm_id", "")))
        batch_table["name"].append(str(item.get("name", "")))
        batch_table["classification"].append(str(item.get("classification", "")))
        batch_table["height_m"].append(float(item.get("height_m", 0.0)))
        batch_table["height_source"].append(str(item.get("height_source", "")))
        batch_table["is_placeholder"].append(bool(item.get("is_placeholder", False)))

    # 3. Assemble glTF JSON structure
    gltf: dict[str, Any] = {
        "asset": {
            "version": "2.0",
            "generator": "GLITTERS 3D Digital Twin Engine 1.0",
        },
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{"mesh": 0}],
        "materials": [
            {
                "name": "ArchitecturalStructureMaterial",
                "pbrMetallicRoughness": {
                    "baseColorFactor": [0.88, 0.86, 0.82, 1.0],
                    "metallicFactor": 0.05,
                    "roughnessFactor": 0.75,
                },
                "doubleSided": False,
            }
        ],
        "meshes": [
            {
                "primitives": [
                    {
                        "attributes": {
                            "POSITION": 1,
                            "NORMAL": 2,
                            "_BATCHID": 3,
                        },
                        "indices": 0,
                        "material": 0,
                        "mode": 4,  # TRIANGLES
                    }
                ]
            }
        ],
        "buffers": [{"byteLength": len(total_bin_bytes)}],
        "bufferViews": [
            {
                "buffer": 0,
                "byteOffset": offset_indices,
                "byteLength": len_indices,
                "target": 34963,  # ELEMENT_ARRAY_BUFFER
            },
            {
                "buffer": 0,
                "byteOffset": offset_positions,
                "byteLength": len_positions,
                "byteStride": 12,
                "target": 34962,  # ARRAY_BUFFER
            },
            {
                "buffer": 0,
                "byteOffset": offset_normals,
                "byteLength": len_normals,
                "byteStride": 12,
                "target": 34962,  # ARRAY_BUFFER
            },
            {
                "buffer": 0,
                "byteOffset": offset_batch_ids,
                "byteLength": len_batch_ids,
                "byteStride": 4,
                "target": 34962,  # ARRAY_BUFFER
            },
        ],
        "accessors": [
            {
                "bufferView": 0,
                "byteOffset": 0,
                "componentType": 5125,  # UNSIGNED_INT
                "count": num_indices,
                "type": "SCALAR",
            },
            {
                "bufferView": 1,
                "byteOffset": 0,
                "componentType": 5126,  # FLOAT
                "count": num_vertices,
                "type": "VEC3",
                "min": mesh.min_bounds,
                "max": mesh.max_bounds,
            },
            {
                "bufferView": 2,
                "byteOffset": 0,
                "componentType": 5126,  # FLOAT
                "count": num_vertices,
                "type": "VEC3",
            },
            {
                "bufferView": 3,
                "byteOffset": 0,
                "componentType": 5126,  # FLOAT
                "count": num_vertices,
                "type": "SCALAR",
            },
        ],
        "extras": {
            "batchTable": batch_table,
        },
    }

    return _wrap_glb(gltf, total_bin_bytes)


def _wrap_glb(gltf_dict: dict[str, Any], bin_bytes: bytes) -> bytes:
    """Wraps glTF JSON and binary payload into standard 12-byte header GLB."""
    json_bytes = json.dumps(gltf_dict, separators=(",", ":")).encode("utf-8")
    json_pad = (4 - (len(json_bytes) % 4)) % 4
    json_bytes += b" " * json_pad

    bin_pad = (4 - (len(bin_bytes) % 4)) % 4
    bin_bytes += b"\x00" * bin_pad

    total_len = 12 + 8 + len(json_bytes)
    if bin_bytes:
        total_len += 8 + len(bin_bytes)

    header = struct.pack("<III", 0x46546C67, 2, total_len)
    chunk0 = struct.pack("<II", len(json_bytes), 0x4E4F534A) + json_bytes

    if bin_bytes:
        chunk1 = struct.pack("<II", len(bin_bytes), 0x004E4942) + bin_bytes
        return header + chunk0 + chunk1

    return header + chunk0


def generate_tileset_json(
    origin_lon: float,
    origin_lat: float,
    origin_height: float,
    min_lon: float,
    min_lat: float,
    max_lon: float,
    max_lat: float,
    min_h: float,
    max_h: float,
    tile_relative_path: str = "tiles/tile_0.glb",
) -> dict[str, Any]:
    """Generates standard OGC 3D Tiles 1.1 tileset.json specification."""
    transform = compute_enu_transform_matrix(origin_lon, origin_lat, origin_height)

    # Bounding region in radians: [west, south, east, north, min_height, max_height]
    west_rad = math.radians(min_lon)
    south_rad = math.radians(min_lat)
    east_rad = math.radians(max_lon)
    north_rad = math.radians(max_lat)

    return {
        "asset": {
            "version": "1.1",
            "generator": "GLITTERS Core 3D Digital Twin Pipeline",
        },
        "geometricError": 250.0,
        "root": {
            "boundingVolume": {
                "region": [west_rad, south_rad, east_rad, north_rad, min_h, max_h],
            },
            "geometricError": 0.0,
            "refine": "REPLACE",
            "transform": transform,
            "content": {
                "uri": tile_relative_path,
            },
        },
    }


def generate_digital_twin_tileset(
    buildings_qs,
    generation_id: uuid.UUID | None = None,
    output_base_dir: Path | None = None,
) -> DigitalTwinGeneration:
    """
    Executes end-to-end 3D reconstruction and tileset generation for a queryset of Building records.
    Persists DigitalTwinGeneration, DigitalTwinAsset, and DigitalTwinAttributeProvenance records.
    """
    start_time = time.perf_counter()

    if generation_id is None:
        generation_id = uuid.uuid4()

    out_dir = (output_base_dir or STORAGE_ROOT) / str(generation_id)
    tiles_dir = out_dir / "tiles"
    tiles_dir.mkdir(parents=True, exist_ok=True)

    # 1. Fetch buildings with geometries
    buildings = list(buildings_qs)
    total_buildings = len(buildings)

    gen_record, _ = DigitalTwinGeneration.objects.get_or_create(
        id=generation_id,
        defaults={
            "status": DigitalTwinStatus.PROCESSING,
            "building_count": total_buildings,
            "tileset_urn": f"urn:glitters:spatial:tileset:{generation_id}",
            "generator_version": "1.0.0",
        },
    )
    gen_record.status = DigitalTwinStatus.PROCESSING
    gen_record.save(update_fields=["status"])

    if total_buildings == 0:
        duration_ms = int((time.perf_counter() - start_time) * 1000)
        gen_record.status = DigitalTwinStatus.COMPLETED
        gen_record.duration_ms = duration_ms
        gen_record.save()
        return gen_record

    try:
        # Calculate overall bounding extent and centroid
        min_lon, min_lat, max_lon, max_lat = float("inf"), float("inf"), -float("inf"), -float("inf")
        for b in buildings:
            if b.geom:
                extent = b.geom.extent  # (xmin, ymin, xmax, ymax)
                if extent[0] < min_lon: min_lon = extent[0]
                if extent[1] < min_lat: min_lat = extent[1]
                if extent[2] > max_lon: max_lon = extent[2]
                if extent[3] > max_lat: max_lat = extent[3]

        origin_lon = (min_lon + max_lon) / 2.0
        origin_lat = (min_lat + max_lat) / 2.0
        origin_height = 0.0

        # Set generation area of interest
        gen_record.area_of_interest = Polygon.from_bbox((min_lon, min_lat, max_lon, max_lat))

        # 2. Extrude each building into MeshData
        mesh = MeshData()
        batch_metadata: list[dict[str, Any]] = []
        asset_records_to_create: list[DigitalTwinAsset] = []
        provenance_to_create: list[dict[str, Any]] = []

        # Pre-fetch external source feature IDs if available
        building_ids = [b.id for b in buildings]
        source_mappings: dict[uuid.UUID, str] = {}
        for sf in SpatialSourceFeature.objects.filter(spatial_entity_id__in=building_ids).only("spatial_entity_id", "source_record_id"):
            source_mappings[sf.spatial_entity_id] = sf.source_record_id

        max_roof_h = 0.0

        for idx, b in enumerate(buildings):
            batch_id = float(idx)
            h, src, placeholder, tri_count = extrude_building(
                b, origin_lon, origin_lat, batch_id, mesh
            )
            if h > max_roof_h:
                max_roof_h = h

            osm_id = source_mappings.get(b.id, "")
            metadata_item = {
                "canonical_urn": b.canonical_urn,
                "building_id": str(b.id),
                "osm_id": osm_id,
                "name": b.name or "",
                "classification": b.classification,
                "height_m": h,
                "height_source": src,
                "is_placeholder": placeholder,
            }
            batch_metadata.append(metadata_item)

            # Prepare asset model
            bbox = Polygon.from_bbox(b.geom.extent) if b.geom else None
            asset_obj = DigitalTwinAsset(
                id=uuid.uuid4(),
                entity=b,
                generation=gen_record,
                format=DigitalTwinFormat.OGC_3DTILES,
                storage_path=str(out_dir / "tileset.json"),
                bounding_box=bbox,
                height_m=h,
                height_source=src,
                is_placeholder=placeholder,
                lod_level=1,
                triangles_count=tri_count,
                vertex_count=tri_count * 3,
            )
            asset_records_to_create.append(asset_obj)

        # 3. Pack GLB binary tile
        glb_bytes = pack_mesh_to_glb(mesh, batch_metadata)
        glb_path = tiles_dir / "tile_0.glb"
        glb_path.write_bytes(glb_bytes)

        # 4. Generate tileset.json
        tileset_dict = generate_tileset_json(
            origin_lon=origin_lon,
            origin_lat=origin_lat,
            origin_height=origin_height,
            min_lon=min_lon,
            min_lat=min_lat,
            max_lon=max_lon,
            max_lat=max_lat,
            min_h=0.0,
            max_h=max_roof_h + 5.0,
            tile_relative_path="tiles/tile_0.glb",
        )
        tileset_path = out_dir / "tileset.json"
        tileset_path.write_text(json.dumps(tileset_dict, indent=2), encoding="utf-8")

        # 5. Persist records in database
        with transaction.atomic():
            # Delete any prior DigitalTwinAsset for these entities
            DigitalTwinAsset.objects.filter(entity__in=buildings).delete()
            created_assets = DigitalTwinAsset.objects.bulk_create(asset_records_to_create)

            # Create provenance records
            prov_objs = []
            for asset, meta in zip(created_assets, batch_metadata):
                prov_objs.append(
                    DigitalTwinAttributeProvenance(
                        asset=asset,
                        attribute_name="height_m",
                        value=str(meta["height_m"]),
                        source=meta["height_source"],
                        confidence_score=0.5 if meta["is_placeholder"] else 0.95,
                        verification_status="SURVEYOR_VERIFIED" if meta["height_source"] == HeightSource.SURVEY_VERIFIED_HEIGHT else "AUTOMATED_INFERENCE",
                    )
                )
                prov_objs.append(
                    DigitalTwinAttributeProvenance(
                        asset=asset,
                        attribute_name="lod_level",
                        value="LoD 1 (Prismatic Block Model)",
                        source="GLITTERS_3D_RECONSTRUCTION_ENGINE",
                        confidence_score=1.0,
                        verification_status="COMPLIANT_OGC_3DTILES",
                    )
                )
            DigitalTwinAttributeProvenance.objects.bulk_create(prov_objs)

            duration_ms = int((time.perf_counter() - start_time) * 1000)
            gen_record.status = DigitalTwinStatus.COMPLETED
            gen_record.tileset_path = str(tileset_path)
            gen_record.duration_ms = duration_ms
            gen_record.save()

        return gen_record

    except Exception as exc:
        duration_ms = int((time.perf_counter() - start_time) * 1000)
        gen_record.status = DigitalTwinStatus.FAILED
        gen_record.error_details = str(exc)
        gen_record.duration_ms = duration_ms
        gen_record.save()
        raise
