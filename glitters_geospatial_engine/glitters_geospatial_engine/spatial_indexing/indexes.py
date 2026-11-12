"""
Spatial Indexing & Query Engine (`spatial_indexing/indexes.py` v1.1.0)
======================================================================

Upgrades in v1.1.0:
- Explicitly separates GLITTERS local axial hexagonal grid identifiers (`GlittersHexGridIndex`,
  prefixed `GHX{res:02d}_...`) and GLITTERS planar Hilbert identifiers (`GlittersHilbertGridIndex`)
  from official Uber H3 and Google S2 library identifiers so custom encodings are never
  misrepresented as interoperable H3/S2 keys in database schemas or contracts.
- Detects at runtime whether official `h3` or `s2sphere` bindings are installed and exposes
  `grid_standard` provenance metadata on every generated spatial key.
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple
import math
import numpy as np
from scipy.spatial import cKDTree
from shapely.geometry import Point, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.strtree import STRtree

try:
    import h3 as _official_h3  # type: ignore[import-not-found]
    HAS_OFFICIAL_H3 = True
except ImportError:
    _official_h3 = None
    HAS_OFFICIAL_H3 = False

try:
    import s2sphere as _official_s2  # type: ignore[import-not-found]
    HAS_OFFICIAL_S2 = True
except ImportError:
    _official_s2 = None
    HAS_OFFICIAL_S2 = False


# -----------------------------------------------------------------------------
# 1. Geohash (Standard Base32 Interleaved Z-order Morton Curve)
# -----------------------------------------------------------------------------
_GEOHASH_BASE32 = "0123456789bcdefghjkmnpqrstuvwxyz"


def encode_geohash(lon: float, lat: float, precision: int = 8) -> str:
    """Encode (lon, lat) in WGS84 degrees into a standard Base32 Geohash string."""
    lat_interval = [-90.0, 90.0]
    lon_interval = [-180.0, 180.0]
    geohash: List[str] = []
    bits = [16, 8, 4, 2, 1]
    bit = 0
    ch = 0
    even = True

    while len(geohash) < precision:
        if even:
            mid = (lon_interval[0] + lon_interval[1]) / 2.0
            if lon >= mid:
                ch |= bits[bit]
                lon_interval[0] = mid
            else:
                lon_interval[1] = mid
        else:
            mid = (lat_interval[0] + lat_interval[1]) / 2.0
            if lat >= mid:
                ch |= bits[bit]
                lat_interval[0] = mid
            else:
                lat_interval[1] = mid
        even = not even
        if bit < 4:
            bit += 1
        else:
            geohash.append(_GEOHASH_BASE32[ch])
            bit = 0
            ch = 0
    return "".join(geohash)


# -----------------------------------------------------------------------------
# 2. GLITTERS Axial Hexagonal Grid Indexer (with Optional Official H3 Support)
# -----------------------------------------------------------------------------
class GlittersHexGridIndex:
    """
    Multi-resolution axial-coordinate hexagonal spatial indexer (`GLITTERS_AXIAL_HEX_GRID_V1`).
    Produces explicitly namespaced identifiers `GHX{res:02d}_{q:06x}_{r:06x}` so they are
    never confused with Uber H3's icosahedral face-IJK bitfields when `h3-py` is absent.
    If the official `h3` library is installed, `encode_official_h3_if_available` also returns
    the standard 15-character Uber H3 index.
    """

    GRID_STANDARD: str = "GLITTERS_AXIAL_HEX_GRID_V1"

    HEX_EDGE_LENGTH_M: Dict[int, float] = {
        0: 1_107_712.59,
        1: 418_676.00,
        2: 158_244.65,
        3: 59_810.86,
        4: 22_606.38,
        5: 8_544.41,
        6: 3_229.48,
        7: 1_220.63,
        8: 461.35,
        9: 174.38,
        10: 65.91,
        11: 24.91,
        12: 9.42,
        13: 3.56,
        14: 1.35,
        15: 0.51,
    }

    @classmethod
    def latlon_to_glitters_hex_id(cls, lat: float, lon: float, resolution: int = 9) -> str:
        """
        Encode WGS84 (lat, lon) into an explicitly namespaced GLITTERS axial hexagonal ID
        `GHX{resolution:02d}_{q:06x}_{r:06x}` (18 characters).
        """
        if not (0 <= resolution <= 15):
            raise ValueError("Hexagonal resolution must be in [0, 15].")
        size = cls.HEX_EDGE_LENGTH_M[resolution]
        x_m = lon * 111_320.0 * math.cos(math.radians(lat))
        y_m = lat * 110_574.0

        q_frac = (math.sqrt(3.0) / 3.0 * x_m - 1.0 / 3.0 * y_m) / size
        r_frac = (2.0 / 3.0 * y_m) / size
        q, r = cls._axial_round(q_frac, r_frac)
        return f"GHX{resolution:02d}_{q & 0xFFFFFF:06x}_{r & 0xFFFFFF:06x}"

    @classmethod
    def latlon_to_hex_id(cls, lat: float, lon: float, resolution: int = 9) -> str:
        """Alias returning the explicitly namespaced GLITTERS hexagonal grid ID."""
        return cls.latlon_to_glitters_hex_id(lat, lon, resolution=resolution)

    @classmethod
    def encode_with_metadata(cls, lat: float, lon: float, resolution: int = 9) -> Dict[str, Any]:
        glitters_id = cls.latlon_to_glitters_hex_id(lat, lon, resolution)
        official_h3_id: Optional[str] = None
        if HAS_OFFICIAL_H3 and _official_h3 is not None:
            official_h3_id = (
                _official_h3.latlng_to_cell(lat, lon, resolution)
                if hasattr(_official_h3, "latlng_to_cell")
                else _official_h3.geo_to_h3(lat, lon, resolution)
            )
        return {
            "glitters_hex_id": glitters_id,
            "grid_standard": cls.GRID_STANDARD,
            "official_uber_h3_id": official_h3_id,
            "is_official_uber_h3": official_h3_id is not None,
        }

    @staticmethod
    def _axial_round(q_f: float, r_f: float) -> Tuple[int, int]:
        s_f = -q_f - r_f
        q = round(q_f)
        r = round(r_f)
        s = round(s_f)
        q_diff = abs(q - q_f)
        r_diff = abs(r - r_f)
        s_diff = abs(s - s_f)
        if q_diff > r_diff and q_diff > s_diff:
            q = -r - s
        elif r_diff > s_diff:
            r = -q - s
        return int(q), int(r)

    @classmethod
    def k_ring_axial(cls, q: int, r: int, k: int = 1) -> List[Tuple[int, int]]:
        results: List[Tuple[int, int]] = []
        for dq in range(-k, k + 1):
            for dr in range(max(-k, -dq - k), min(k, -dq + k) + 1):
                results.append((q + dq, r + dr))
        return results


# Backward-compatible alias
HexagonalHierarchicalIndex = GlittersHexGridIndex


# -----------------------------------------------------------------------------
# 3. GLITTERS Planar Hilbert Curve Indexer (with Optional Official S2 Support)
# -----------------------------------------------------------------------------
class GlittersHilbertGridIndex:
    """
    Maps geographic or planar coordinates onto a 64-bit Hilbert space-filling curve index
    (`GLITTERS_PLANAR_HILBERT_V1`) for locality-preserving table clustering (`CLUSTER USING`)
    and BRIN block-range index optimization. Explicitly distinguished from Google S2 spherical
    cube-face cell IDs unless `s2sphere` is installed.
    """

    GRID_STANDARD: str = "GLITTERS_PLANAR_HILBERT_V1"

    @staticmethod
    def xy_to_hilbert(ix: int, iy: int, order: int = 16) -> int:
        d = 0
        s = 1 << (order - 1)
        x, y = ix, iy
        while s > 0:
            rx = 1 if (x & s) > 0 else 0
            ry = 1 if (y & s) > 0 else 0
            d += s * s * ((3 * rx) ^ ry)
            if ry == 0:
                if rx == 1:
                    x = s - 1 - x
                    y = s - 1 - y
                x, y = y, x
            s >>= 1
        return int(d)

    @classmethod
    def encode_hilbert_cell_id(cls, lon: float, lat: float, level: int = 16) -> int:
        grid_max = (1 << level) - 1
        nx = int(np.clip((lon + 180.0) / 360.0 * grid_max, 0, grid_max))
        ny = int(np.clip((lat + 90.0) / 180.0 * grid_max, 0, grid_max))
        return cls.xy_to_hilbert(nx, ny, order=level)

    @classmethod
    def encode_s2_cell_id(cls, lon: float, lat: float, level: int = 16) -> int:
        """Backward-compatible alias for `encode_hilbert_cell_id`."""
        return cls.encode_hilbert_cell_id(lon, lat, level=level)

    @classmethod
    def encode_with_metadata(cls, lon: float, lat: float, level: int = 16) -> Dict[str, Any]:
        hilbert_id = cls.encode_hilbert_cell_id(lon, lat, level=level)
        official_s2_id: Optional[int] = None
        if HAS_OFFICIAL_S2 and _official_s2 is not None:
            ll = _official_s2.LatLng.from_degrees(lat, lon)
            official_s2_id = int(_official_s2.CellId.from_lat_lng(ll).parent(level).id())
        return {
            "glitters_hilbert_id": hilbert_id,
            "grid_standard": cls.GRID_STANDARD,
            "official_google_s2_id": official_s2_id,
            "is_official_google_s2": official_s2_id is not None,
        }


# Backward-compatible alias
S2HilbertIndex = GlittersHilbertGridIndex


# -----------------------------------------------------------------------------
# 4. Planar Quadtree Index
# -----------------------------------------------------------------------------
class PlanarQuadtree:
    def __init__(
        self,
        bounds: Tuple[float, float, float, float],
        max_items: int = 16,
        max_depth: int = 10,
        _depth: int = 0,
    ) -> None:
        self.bounds = bounds
        self.max_items = max_items
        self.max_depth = max_depth
        self._depth = _depth
        self.items: List[Tuple[int, Tuple[float, float, float, float]]] = []
        self.children: Optional[List[PlanarQuadtree]] = None

    @staticmethod
    def _boxes_intersect(
        a: Tuple[float, float, float, float], b: Tuple[float, float, float, float]
    ) -> bool:
        return not (a[2] < b[0] or a[0] > b[2] or a[3] < b[1] or a[1] > b[3])

    def _subdivide(self) -> None:
        minx, miny, maxx, maxy = self.bounds
        midx = 0.5 * (minx + maxx)
        midy = 0.5 * (miny + maxy)
        nd = self._depth + 1
        self.children = [
            PlanarQuadtree((minx, miny, midx, midy), self.max_items, self.max_depth, nd),
            PlanarQuadtree((midx, miny, maxx, midy), self.max_items, self.max_depth, nd),
            PlanarQuadtree((minx, midy, midx, maxy), self.max_items, self.max_depth, nd),
            PlanarQuadtree((midx, midy, maxx, maxy), self.max_items, self.max_depth, nd),
        ]

    def insert(self, item_id: int, bbox: Tuple[float, float, float, float]) -> None:
        if not self._boxes_intersect(self.bounds, bbox):
            return
        if self.children is not None:
            for child in self.children:
                child.insert(item_id, bbox)
            return
        self.items.append((item_id, bbox))
        if len(self.items) > self.max_items and self._depth < self.max_depth:
            self._subdivide()
            old_items = self.items
            self.items = []
            for oid, obbox in old_items:
                for child in self.children:  # type: ignore[union-attr]
                    child.insert(oid, obbox)

    def query_bbox(self, search_bbox: Tuple[float, float, float, float]) -> List[int]:
        if not self._boxes_intersect(self.bounds, search_bbox):
            return []
        found: List[int] = []
        for iid, ibbox in self.items:
            if self._boxes_intersect(ibbox, search_bbox):
                found.append(iid)
        if self.children is not None:
            for child in self.children:
                found.extend(child.query_bbox(search_bbox))
        return sorted(set(found))


# -----------------------------------------------------------------------------
# 5. BRIN (Block Range Index) Simulator
# -----------------------------------------------------------------------------
@dataclass(frozen=True)
class BRINPageSummary:
    block_id: int
    start_idx: int
    end_idx: int
    minx: float
    miny: float
    maxx: float
    maxy: float


class BRINSpatialIndex:
    def __init__(self, bboxes: np.ndarray, pages_per_range: int = 128) -> None:
        self.bboxes = bboxes
        self.pages_per_range = pages_per_range
        self.summaries: List[BRINPageSummary] = []
        n = len(bboxes)
        for b_id, start in enumerate(range(0, n, pages_per_range)):
            end = min(n, start + pages_per_range)
            chunk = bboxes[start:end]
            self.summaries.append(
                BRINPageSummary(
                    block_id=b_id,
                    start_idx=start,
                    end_idx=end,
                    minx=float(np.min(chunk[:, 0])),
                    miny=float(np.min(chunk[:, 1])),
                    maxx=float(np.max(chunk[:, 2])),
                    maxy=float(np.max(chunk[:, 3])),
                )
            )

    def query_bbox(self, qbox: Tuple[float, float, float, float]) -> Tuple[List[int], int]:
        qminx, qminy, qmaxx, qmaxy = qbox
        matches: List[int] = []
        blocks_scanned = 0
        for s in self.summaries:
            if not (s.maxx < qminx or s.minx > qmaxx or s.maxy < qminy or s.miny > qmaxy):
                blocks_scanned += 1
                chunk = self.bboxes[s.start_idx : s.end_idx]
                mask = (
                    (chunk[:, 2] >= qminx)
                    & (chunk[:, 0] <= qmaxx)
                    & (chunk[:, 3] >= qminy)
                    & (chunk[:, 1] <= qmaxy)
                )
                for offset in np.nonzero(mask)[0]:
                    matches.append(s.start_idx + int(offset))
        return matches, blocks_scanned


# -----------------------------------------------------------------------------
# 6. Unified Spatial Query Engine
# -----------------------------------------------------------------------------
class SpatialIndexManager:
    def __init__(self, geometries_utm: Sequence[BaseGeometry]) -> None:
        self.geometries = list(geometries_utm)
        self.strtree = STRtree(self.geometries)
        self.centroids_xy = np.array(
            [[g.centroid.x, g.centroid.y] for g in self.geometries], dtype=np.float64
        )
        self.kdtree = cKDTree(self.centroids_xy) if len(self.geometries) > 0 else None

    def radius_search(self, center_xy: Tuple[float, float], radius_m: float) -> List[int]:
        pt = Point(center_xy)
        search_circle = pt.buffer(radius_m)
        candidates = self.strtree.query(search_circle, predicate="intersects")
        return [int(idx) for idx in candidates if self.geometries[int(idx)].distance(pt) <= radius_m]

    def polygon_intersection_query(self, query_poly: Polygon) -> List[int]:
        return [int(i) for i in self.strtree.query(query_poly, predicate="intersects")]

    def spatial_join_intersects(
        self, right_geometries: Sequence[BaseGeometry]
    ) -> List[Tuple[int, int]]:
        pairs: List[Tuple[int, int]] = []
        for r_idx, r_geom in enumerate(right_geometries):
            l_matches = self.strtree.query(r_geom, predicate="intersects")
            for l_idx in l_matches:
                pairs.append((int(l_idx), int(r_idx)))
        return pairs

    def nearest_k_properties(self, query_xy: Tuple[float, float], k: int = 5) -> List[Tuple[int, float]]:
        if self.kdtree is None or len(self.geometries) == 0:
            return []
        cand_k = min(len(self.geometries), max(k * 4, 20))
        _, idxs = self.kdtree.query(query_xy, k=cand_k)
        if np.isscalar(idxs):
            idxs = [int(idxs)]
        pt = Point(query_xy)
        refined = [(int(i), float(self.geometries[int(i)].distance(pt))) for i in idxs]
        refined.sort(key=lambda x: x[1])
        return refined[:k]
