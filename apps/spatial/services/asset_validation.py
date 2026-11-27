"""Structural checks for COG, GLB, terrain bundles, and 3D Tiles bundles.

Invalid files are rejected before they are treated as published. These checks do
not certify a survey, a photogrammetric model, or a bare-earth terrain.
"""

from __future__ import annotations

import json
import math
import struct

from apps.spatial.services.compute.contracts import PermanentComputeError
from apps.spatial.services.compute.gltf_job import sha256_hex
from apps.spatial.services.quantized_mesh import decode_mesh

_TYPE_SIZE = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 12: 8}
_GEO_KEY = 34735
_MODEL_SCALE = 33550
_MODEL_TIE = 33922
_GEOGRAPHIC = 2048
_PROJECTED = 3072
_MAX_ACCESSOR = 5_000_000
_EXTERNAL = ("http://", "https://", "file:", "data:", "//")


def _all_finite(values: object) -> bool:
    if not isinstance(values, list):
        return False
    return all(isinstance(item, (int, float)) and math.isfinite(float(item)) for item in values)


def _tiff_numbers(payload: bytes, endian: str, type_id: int, count: int, field: bytes) -> list:
    size = _TYPE_SIZE.get(type_id)
    if size is None or count < 1 or count > 1_000_000:
        raise PermanentComputeError("The TIFF field type is not supported.")
    total = size * count
    if total <= 4:
        raw = field[:total]
    else:
        (pointer,) = struct.unpack_from(endian + "I", field)
        if pointer < 8 or pointer + total > len(payload):
            raise PermanentComputeError("A TIFF field points outside the file.")
        raw = payload[pointer : pointer + total]
    if type_id == 2:
        return [raw.split(b"\x00", 1)[0].decode("latin-1", errors="replace")]
    code = {1: "B", 3: "H", 4: "I", 5: "I", 12: "d"}[type_id]
    repeat = count * (2 if type_id == 5 else 1)
    return list(struct.unpack_from(endian + code * repeat, raw))


def _ifd(payload: bytes, endian: str, offset: int) -> dict[int, list]:
    if offset < 8 or offset + 2 > len(payload):
        raise PermanentComputeError("The TIFF directory is truncated.")
    (count,) = struct.unpack_from(endian + "H", payload, offset)
    table = offset + 2
    if count > 4096 or table + count * 12 + 4 > len(payload):
        raise PermanentComputeError("The TIFF directory is truncated.")
    fields: dict[int, list] = {}
    for index in range(count):
        entry = table + index * 12
        tag, type_id, value_count = struct.unpack_from(endian + "HHI", payload, entry)
        field = payload[entry + 8 : entry + 12]
        try:
            fields[tag] = _tiff_numbers(payload, endian, type_id, value_count, field)
        except PermanentComputeError:
            raise
        except struct.error as exc:
            raise PermanentComputeError("A TIFF field could not be read.") from exc
    (next_offset,) = struct.unpack_from(endian + "I", payload, table + count * 12)
    if next_offset and (next_offset < 8 or next_offset >= len(payload)):
        raise PermanentComputeError("The next TIFF directory is outside the file.")
    fields[-1] = [next_offset]
    return fields


def _one(fields: dict[int, list], tag: int, default: float | None = None) -> float:
    values = fields.get(tag)
    if not values:
        if default is None:
            raise PermanentComputeError(f"TIFF tag {tag} is missing.")
        return default
    return float(values[0])


def validate_cog(
    payload: bytes,
    *,
    expected_bounds: tuple[float, float, float, float] | None = None,
) -> dict:
    """Require a tiled TIFF with readable tile bytes, CRS metadata, and finite bounds."""

    if len(payload) < 16:
        raise PermanentComputeError("The COG is truncated.")
    if payload.startswith(b"II*\x00"):
        endian = "<"
    elif payload.startswith(b"MM\x00*"):
        endian = ">"
    else:
        raise PermanentComputeError("A TIFF header was not found. The file was not stored.")
    (first,) = struct.unpack_from(endian + "I", payload, 4)
    fields = _ifd(payload, endian, first)
    width = int(_one(fields, 256))
    height = int(_one(fields, 257))
    if width < 1 or height < 1 or width > 200_000 or height > 200_000:
        raise PermanentComputeError("The raster dimensions are not usable.")
    bits = int(_one(fields, 258))
    samples = int(_one(fields, 277, 1))
    if bits not in {8, 16, 32, 64} or samples < 1 or samples > 16:
        raise PermanentComputeError("The raster sample format is not supported.")
    tile_width = fields.get(322)
    tile_height = fields.get(323)
    offsets = fields.get(324)
    counts = fields.get(325)
    if not tile_width or not tile_height or not offsets or not counts:
        raise PermanentComputeError("A COG must be tiled. Strip-only rasters are not accepted.")
    if len(offsets) != len(counts) or not offsets:
        raise PermanentComputeError("The COG tile index is inconsistent.")
    across = math.ceil(width / int(tile_width[0]))
    down = math.ceil(height / int(tile_height[0]))
    if across * down != len(offsets):
        raise PermanentComputeError("The COG tile count does not match the raster size.")
    raw_bytes = int(tile_width[0]) * int(tile_height[0]) * samples * (bits // 8)
    compression = int(_one(fields, 259, 1))
    for offset, count in zip(offsets, counts, strict=True):
        start = int(offset)
        size = int(count)
        if size < 1 or start < 8 or start + size > len(payload):
            raise PermanentComputeError("A COG tile window is truncated or empty.")
        if compression == 1 and size != raw_bytes:
            raise PermanentComputeError("An uncompressed COG tile is not the expected size.")
    window = payload[int(offsets[0]) : int(offsets[0]) + int(counts[0])]
    if len(window) != int(counts[0]):
        raise PermanentComputeError("The first COG tile window could not be read.")
    scale = fields.get(_MODEL_SCALE)
    tie = fields.get(_MODEL_TIE)
    if not scale or len(scale) < 2 or not tie or len(tie) < 6:
        raise PermanentComputeError("The COG has no geotransform.")
    keys = fields.get(_GEO_KEY)
    if not keys or len(keys) < 8:
        raise PermanentComputeError("The COG has no CRS metadata.")
    geographic = None
    projected = None
    pairs = (len(keys) - 4) // 4
    for index in range(pairs):
        key_id, location, _count, value = keys[4 + index * 4 : 8 + index * 4]
        if location == 0 and key_id == _GEOGRAPHIC:
            geographic = int(value)
        if location == 0 and key_id == _PROJECTED:
            projected = int(value)
    if geographic is None and projected is None:
        raise PermanentComputeError("The COG CRS key does not name a horizontal CRS.")
    sx, sy = float(scale[0]), float(scale[1])
    if not math.isfinite(sx) or not math.isfinite(sy) or sx == 0 or sy == 0:
        raise PermanentComputeError("The COG pixel scale is not finite.")
    pixel_i, pixel_j = float(tie[0]), float(tie[1])
    origin_x, origin_y = float(tie[3]), float(tie[4])
    west = origin_x - pixel_i * sx
    north = origin_y + pixel_j * sy
    east = west + width * sx
    south = north - height * sy
    if not all(math.isfinite(value) for value in (west, south, east, north)):
        raise PermanentComputeError("The COG bounds are not finite.")
    outside = west < -180.5 or east > 180.5 or south < -90.5 or north > 90.5
    outside = outside or east <= west or north <= south
    if geographic == 4326 and outside:
        raise PermanentComputeError("The COG bounds are outside the geographic CRS.")
    if expected_bounds is not None and geographic == 4326:
        bound_west, bound_south, bound_east, bound_north = expected_bounds
        misses = east < bound_west - 0.02 or west > bound_east + 0.02
        misses = misses or north < bound_south - 0.02 or south > bound_north + 0.02
        if misses:
            raise PermanentComputeError("The COG bounds do not meet the expected window.")
    return {
        "width": width,
        "height": height,
        "crs": geographic or projected,
        "bounds": (west, south, east, north),
        "tiles": len(offsets),
    }


def _json_chunk(payload: bytes, start: int, length: int) -> dict:
    if start < 0 or length < 2 or start + length > len(payload):
        raise PermanentComputeError("A GLB JSON chunk is truncated.")
    try:
        document = json.loads(payload[start : start + length])
    except json.JSONDecodeError as exc:
        raise PermanentComputeError("A GLB JSON chunk is not valid JSON.") from exc
    if not isinstance(document, dict):
        raise PermanentComputeError("A GLB JSON chunk must be an object.")
    return document


def _reject_external(value: object, label: str) -> None:
    if not isinstance(value, str) or not value:
        return
    lowered = value.strip().lower()
    if lowered.startswith(_EXTERNAL) or "://" in lowered or lowered.startswith(("/", "\\")):
        raise PermanentComputeError(f"The GLB {label} references an external resource.")
    if any(part == ".." for part in value.replace("\\", "/").split("/")):
        raise PermanentComputeError(f"The GLB {label} references an external resource.")


def validate_glb(payload: bytes) -> dict:
    """Parse one GLB. External buffer, image, and shader references are rejected."""

    if len(payload) < 20 or not payload.startswith(b"glTF"):
        raise PermanentComputeError("glTF binary magic was not found. The file was not stored.")
    _magic, version, length = struct.unpack_from("<4sII", payload, 0)
    if version != 2 or length != len(payload) or length > 80_000_000:
        raise PermanentComputeError("The GLB length or version is not valid.")
    offset = 12
    json_document = None
    binary = b""
    while offset + 8 <= len(payload):
        chunk_length, chunk_type = struct.unpack_from("<II", payload, offset)
        offset += 8
        if chunk_length < 0 or offset + chunk_length > len(payload):
            raise PermanentComputeError("A GLB chunk is truncated.")
        chunk = payload[offset : offset + chunk_length]
        offset += chunk_length
        if chunk_type == 0x4E4F534A:
            json_document = _json_chunk(chunk, 0, len(chunk.rstrip(b" \x00")))
        elif chunk_type == 0x004E4942:
            binary = chunk
    if not isinstance(json_document, dict):
        raise PermanentComputeError("The GLB has no JSON chunk.")
    buffers = json_document.get("buffers") or []
    if not isinstance(buffers, list) or not buffers:
        raise PermanentComputeError("The GLB has no buffer.")
    for buffer in buffers:
        if not isinstance(buffer, dict):
            raise PermanentComputeError("A GLB buffer is not an object.")
        _reject_external(buffer.get("uri"), "buffer")
        if "uri" in buffer:
            raise PermanentComputeError(
                "The GLB buffer must be embedded. External buffers are rejected."
            )
        declared = buffer.get("byteLength")
        if not isinstance(declared, int) or declared < 0 or declared > len(binary):
            raise PermanentComputeError("The GLB buffer length does not match the binary chunk.")
    views = json_document.get("bufferViews") or []
    accessors = json_document.get("accessors") or []
    if not isinstance(views, list) or not isinstance(accessors, list):
        raise PermanentComputeError("The GLB accessors are not a list.")
    for view in views:
        if not isinstance(view, dict):
            raise PermanentComputeError("A GLB buffer view is not an object.")
        start = int(view.get("byteOffset") or 0)
        size = int(view.get("byteLength") or 0)
        if start < 0 or size < 0 or start + size > len(binary):
            raise PermanentComputeError("A GLB buffer view is outside the binary chunk.")
    component = {5120: 1, 5121: 1, 5122: 2, 5123: 2, 5125: 4, 5126: 4}
    elements = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}
    for accessor in accessors:
        if not isinstance(accessor, dict):
            raise PermanentComputeError("A GLB accessor is not an object.")
        count = accessor.get("count")
        kind = accessor.get("type")
        code = accessor.get("componentType")
        if not isinstance(count, int) or count < 0 or count > _MAX_ACCESSOR:
            raise PermanentComputeError("A GLB accessor count is not usable.")
        if kind not in elements or code not in component:
            raise PermanentComputeError("A GLB accessor type is not supported.")
        if accessor.get("bufferView") is None:
            continue
        view_index = accessor.get("bufferView")
        if not isinstance(view_index, int) or view_index < 0 or view_index >= len(views):
            raise PermanentComputeError("A GLB accessor points at a missing buffer view.")
        view = views[view_index]
        start = int(view.get("byteOffset") or 0) + int(accessor.get("byteOffset") or 0)
        width = component[code] * elements[kind]
        need = start + count * width
        if need > int(view.get("byteOffset") or 0) + int(view.get("byteLength") or 0):
            raise PermanentComputeError("A GLB accessor reads past its buffer view.")
        if need > len(binary):
            raise PermanentComputeError("A GLB accessor reads past the binary chunk.")
        if kind == "VEC3" and accessor.get("min") and accessor.get("max"):
            if len(accessor["min"]) != 3 or len(accessor["max"]) != 3:
                raise PermanentComputeError("A GLB position min/max is not a vec3.")
            if not _all_finite(accessor["min"]) or not _all_finite(accessor["max"]):
                raise PermanentComputeError("A GLB position bound is not finite.")
    for image in json_document.get("images") or []:
        if isinstance(image, dict):
            _reject_external(image.get("uri"), "image")
            if image.get("uri"):
                raise PermanentComputeError("The GLB image must be embedded.")
    for shader in json_document.get("shaders") or []:
        if isinstance(shader, dict):
            _reject_external(shader.get("uri"), "shader")
    for node in json_document.get("nodes") or []:
        if not isinstance(node, dict):
            continue
        matrix = node.get("matrix")
        if matrix is not None:
            if not isinstance(matrix, list) or len(matrix) != 16:
                raise PermanentComputeError("A GLB node matrix is not 16 numbers.")
            if not _all_finite(matrix):
                raise PermanentComputeError("A GLB node matrix is not finite.")
        for key in ("translation", "scale"):
            value = node.get(key)
            if value is not None and (not isinstance(value, list) or len(value) != 3):
                raise PermanentComputeError("A GLB node transform is not finite.")
            if value is not None and not _all_finite(value):
                raise PermanentComputeError("A GLB node transform is not finite.")
    meshes = json_document.get("meshes") or []
    if not meshes:
        raise PermanentComputeError("The GLB has no mesh.")
    return {"meshes": len(meshes), "accessors": len(accessors), "bytes": len(binary)}


def _bundle_rows(files: dict[str, bytes]) -> list[dict]:
    manifest = files.get("manifest.json")
    if not manifest:
        raise PermanentComputeError("The bundle manifest is missing.")
    try:
        document = json.loads(manifest)
    except json.JSONDecodeError as exc:
        raise PermanentComputeError("The bundle manifest is not JSON.") from exc
    rows = document.get("files") if isinstance(document, dict) else None
    if document.get("format") != "glitters-bundle-v1" or not isinstance(rows, list) or not rows:
        raise PermanentComputeError("The bundle manifest is not a file list.")
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            raise PermanentComputeError("A bundle manifest row is not an object.")
        path = row.get("path")
        digest = row.get("sha256")
        size = row.get("bytes")
        if not isinstance(path, str) or not isinstance(digest, str) or not isinstance(size, int):
            raise PermanentComputeError("A bundle manifest row is incomplete.")
        parts = path.split("/")
        if path in seen or any(part in {"", ".", ".."} for part in parts):
            raise PermanentComputeError("A bundle path is not safe.")
        payload = files.get(path)
        if payload is None:
            raise PermanentComputeError("The bundle manifest names a missing file.")
        if len(payload) != size or sha256_hex(payload) != digest:
            raise PermanentComputeError("A bundle file hash does not match the manifest.")
        seen.add(path)
    extra = set(files) - seen - {"manifest.json"}
    if extra:
        raise PermanentComputeError("The bundle contains a file the manifest does not list.")
    return rows


def validate_terrain_bundle(files: dict[str, bytes]) -> dict:
    rows = _bundle_rows(files)
    layer_bytes = files.get("layer.json")
    vertical_bytes = files.get("vertical.json")
    if layer_bytes is None or vertical_bytes is None:
        raise PermanentComputeError("Terrain tiles need layer.json and vertical.json.")
    layer = json.loads(layer_bytes)
    vertical = json.loads(vertical_bytes)
    if layer.get("format") != "quantized-mesh-1.0" or layer.get("scheme") != "tms":
        raise PermanentComputeError("The terrain layer is not quantized-mesh on the TMS scheme.")
    if layer.get("projection") != "EPSG:4326":
        raise PermanentComputeError("The terrain layer projection is not EPSG:4326.")
    if vertical.get("source_kind") != "DSM" or vertical.get("survey") is not False:
        raise PermanentComputeError("Terrain metadata must stay an unsurveyed DSM.")
    if vertical.get("bare_earth") is not False or vertical.get("photogrammetry") is not False:
        raise PermanentComputeError("Terrain metadata must not claim bare earth or photogrammetry.")
    geoid_applied = vertical.get("geoid_applied_to_mesh") is True
    edge_honest = vertical.get("edge_is_survey") is False
    if not geoid_applied or not edge_honest:
        raise PermanentComputeError("The terrain edge or geoid metadata is not honest.")
    if vertical.get("estimated_ground_elevation_m") is not None:
        raise PermanentComputeError("A ground elevation was claimed without a bare-earth model.")
    available = layer.get("available")
    if not isinstance(available, list) or not available:
        raise PermanentComputeError("The terrain layer has no availability index.")
    listed = {row["path"] for row in rows}
    tiles = 0
    for level, ranges in enumerate(available):
        if not isinstance(ranges, list):
            raise PermanentComputeError("A terrain availability level is not a list.")
        for item in ranges:
            if not isinstance(item, dict):
                raise PermanentComputeError("A terrain availability range is not an object.")
            for y_index in range(int(item["startY"]), int(item["endY"]) + 1):
                for x_index in range(int(item["startX"]), int(item["endX"]) + 1):
                    path = f"{level}/{x_index}/{y_index}.terrain"
                    payload = files.get(path)
                    if payload is None or path not in listed:
                        raise PermanentComputeError("An available terrain tile is missing.")
                    decode_mesh(payload)
                    tiles += 1
    if tiles < 1:
        raise PermanentComputeError("The terrain bundle has no tiles.")
    return {"tiles": tiles, "files": len(rows)}


def _walk_tileset(tile: dict, files: dict[str, bytes], seen: list[str]) -> None:
    content = tile.get("content")
    if isinstance(content, dict) and isinstance(content.get("uri"), str):
        uri = content["uri"]
        if "://" in uri or uri.startswith(("/", "\\")) or ".." in uri.split("/"):
            raise PermanentComputeError("A 3D Tiles content URI is not inside the bundle.")
        payload = files.get(uri)
        if payload is None:
            raise PermanentComputeError("A 3D Tiles content file is missing.")
        if not payload.startswith(b"b3dm"):
            raise PermanentComputeError("A 3D Tiles content file is not b3dm.")
        _validate_b3dm(payload)
        seen.append(uri)
    for child in tile.get("children") or []:
        if isinstance(child, dict):
            _walk_tileset(child, files, seen)


def _validate_b3dm(payload: bytes) -> dict:
    if len(payload) < 28 or not payload.startswith(b"b3dm"):
        raise PermanentComputeError("b3dm magic was not found.")
    header = struct.unpack_from("<4s6I", payload, 0)
    _magic, version, length, feature_len, feature_bin, batch_len, batch_bin = header
    if version != 1 or length != len(payload):
        raise PermanentComputeError("The b3dm header length does not match the file.")
    start = 28
    try:
        feature = json.loads(payload[start : start + feature_len])
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise PermanentComputeError("The b3dm feature table is not JSON.") from exc
    if not isinstance(feature, dict):
        raise PermanentComputeError("The b3dm feature table is not JSON.")
    start += feature_len + feature_bin
    try:
        batch = json.loads(payload[start : start + batch_len]) if batch_len else {}
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise PermanentComputeError("The b3dm batch table is not JSON.") from exc
    start += batch_len + batch_bin
    if start % 4:
        raise PermanentComputeError("The embedded GLB is not 4-byte aligned.")
    glb = payload[start:]
    validate_glb(glb)
    batch_length = int(feature.get("BATCH_LENGTH") or 0)
    if batch_length < 1:
        raise PermanentComputeError("The b3dm feature table has no batches.")
    measured = batch.get("height_measured") if isinstance(batch, dict) else None
    honest = isinstance(measured, list) and len(measured) == batch_length
    honest = honest and all(item is False for item in measured)
    if not honest:
        raise PermanentComputeError("A b3dm batch labeled a height as measured.")
    kinds = batch.get("model_kind")
    if not isinstance(kinds, list) or any(item != "extruded_footprint" for item in kinds):
        raise PermanentComputeError("A b3dm batch is not labeled as an extruded footprint.")
    bases = batch.get("height_basis")
    allowed = {"OSM_HEIGHT_TAG", "ASSUMED_FROM_LEVELS", "ASSUMED_SINGLE_STOREY"}
    if not isinstance(bases, list) or any(item not in allowed for item in bases):
        raise PermanentComputeError("A b3dm height basis is not one of the known sources.")
    return {"batches": batch_length}


def validate_tileset_bundle(files: dict[str, bytes]) -> dict:
    rows = _bundle_rows(files)
    raw = files.get("tileset.json")
    if raw is None:
        raise PermanentComputeError("The 3D Tiles bundle has no tileset.json.")
    document = json.loads(raw)
    asset_block = document.get("asset")
    version = asset_block.get("version") if isinstance(asset_block, dict) else None
    if version != "1.0":
        raise PermanentComputeError("3D Tiles asset.version 1.0 is required.")
    root = document.get("root")
    if not isinstance(root, dict) or "boundingVolume" not in root:
        raise PermanentComputeError("The tileset root has no bounding volume.")
    region = (root.get("boundingVolume") or {}).get("region")
    if not isinstance(region, list) or len(region) != 6:
        raise PermanentComputeError("The tileset root region is not six numbers.")
    finite = all(isinstance(item, (int, float)) and math.isfinite(float(item)) for item in region)
    if not finite:
        raise PermanentComputeError("The tileset root region is not finite.")
    if float(region[5]) < float(region[4]):
        raise PermanentComputeError("The tileset height range is inverted.")
    extras = document.get("extras") if isinstance(document.get("extras"), dict) else {}
    if extras.get("photogrammetry") is not False or extras.get("reconstruction") is not False:
        raise PermanentComputeError("Extruded footprints were labeled as photogrammetry.")
    extruded = extras.get("representation") == "extruded-footprint"
    if not extruded or extras.get("survey") is not False:
        raise PermanentComputeError(
            "The tileset does not identify itself as an extruded footprint."
        )
    seen: list[str] = []
    _walk_tileset(root, files, seen)
    if not seen:
        raise PermanentComputeError("The tileset has no content.")
    listed = {row["path"] for row in rows}
    if any(path not in listed for path in seen):
        raise PermanentComputeError("A tileset content file is missing from the manifest.")
    return {"content": seen, "files": len(rows)}
