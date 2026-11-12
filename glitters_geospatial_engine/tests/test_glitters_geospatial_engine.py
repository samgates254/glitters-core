"""
Complete Unit, Property-Based, Adversarial, and Audit-Regression Test Suite (`v1.1.0`)
======================================================================================

Tests all 15 subpackages, all Section 12 adversarial conditions, and all 10 Critical/Major
Audit Remediation items from the v1.0.0 Technical Audit.
"""

from __future__ import annotations
import hashlib
import math
from pathlib import Path
import struct
import tempfile
import unittest
import numpy as np
from shapely.geometry import LineString, Point, Polygon, box

from glitters_geospatial_engine.crs.kenya_crs import (
    GeodeticMetricCalculationError,
    KenyaAdminHierarchyTag,
    KenyaCRSEngine,
    ObservationAuthority,
    SUPPORTED_KENYA_METRIC_EPSG_CODES,
)
from glitters_geospatial_engine.geometry.algorithms import ComputationalGeometryEngine
from glitters_geospatial_engine.parcels.intelligence import (
    CadastralVerificationEvidence,
    ParcelIntelligenceEngine,
    ParcelRecord,
)
from glitters_geospatial_engine.spatial_indexing.indexes import (
    GlittersHexGridIndex,
    GlittersHilbertGridIndex,
    PlanarQuadtree,
    SpatialIndexManager,
    encode_geohash,
)
from glitters_geospatial_engine.raster.pipeline import (
    AffineGeotransform,
    GeoRasterTile,
    RasterProcessingPipeline,
)
from glitters_geospatial_engine.remote_sensing.indices import SpectralIndicesEngine
from glitters_geospatial_engine.building_detection.detector import (
    BuildingAndLandDetectionEngine,
    InsufficientSpatialResolutionError,
)
from glitters_geospatial_engine.change_detection.temporal_engine import (
    TemporalChangeDetectionEngine,
)
from glitters_geospatial_engine.terrain.hydrology import TerrainAndHydrologyEngine
from glitters_geospatial_engine.network_analysis.routing import SpatialNetworkEngine
from glitters_geospatial_engine.spatial_features.feature_vector import (
    PropertySpatialIntelligenceExtractor,
)
from glitters_geospatial_engine.contracts.glitters_core import (
    ContractComplianceViolationError,
    GlittersCoreParcelIntelligencePayload,
)
from glitters_geospatial_engine.datasets.synthetic_kenya import (
    SyntheticKenyaDatasetGenerator,
)


class TestKenyaCRSAndAuditCritical1And2(unittest.TestCase):
    def setUp(self) -> None:
        self.crs = KenyaCRSEngine()

    def test_critical_1_kenya_utm_zone_selection_all_quadrants_wgs84_and_arc1960(self) -> None:
        # WGS84 all 4 quadrants
        self.assertEqual(self.crs.select_kenya_utm_epsg(36.8219, -1.2921, datum="WGS84"), 32737)
        self.assertEqual(self.crs.select_kenya_utm_epsg(34.7680, -0.0917, datum="WGS84"), 32736)
        self.assertEqual(self.crs.select_kenya_utm_epsg(35.5970, 3.1190, datum="WGS84"), 32636)
        self.assertEqual(self.crs.select_kenya_utm_epsg(37.9899, 2.3284, datum="WGS84"), 32637)

        # Audit CRITICAL 1 exact table reproduction for Arc 1960:
        # (35.5E, 2N) -> EPSG:21096 (Arc 1960 / UTM zone 36N)
        self.assertEqual(self.crs.select_kenya_utm_epsg(35.5, 2.0, datum="ARC1960"), 21096)
        # (35.5E, 2S) -> EPSG:21036 (Arc 1960 / UTM zone 36S)
        self.assertEqual(self.crs.select_kenya_utm_epsg(35.5, -2.0, datum="ARC1960"), 21036)
        # (37.0E, 2N) -> EPSG:21097 (Arc 1960 / UTM zone 37N)
        self.assertEqual(self.crs.select_kenya_utm_epsg(37.0, 2.0, datum="ARC1960"), 21097)
        # (37.0E, 2S) -> EPSG:21037 (Arc 1960 / UTM zone 37S)
        self.assertEqual(self.crs.select_kenya_utm_epsg(37.0, -2.0, datum="ARC1960"), 21037)
        self.assertIn(21096, SUPPORTED_KENYA_METRIC_EPSG_CODES)

    def test_critical_2_rejects_geographic_degrees_and_us_survey_feet_epsg2263(self) -> None:
        # Geographic CRS in degrees must fail
        with self.assertRaises(GeodeticMetricCalculationError):
            self.crs.assert_projected_crs(4326)
        with self.assertRaises(GeodeticMetricCalculationError):
            self.crs.assert_projected_crs(4210)

        # Audit CRITICAL 2 reproduction: EPSG:2263 (NAD83 / New York Long Island in US survey feet)
        with self.assertRaises(GeodeticMetricCalculationError):
            self.crs.assert_projected_crs(2263)

        # GeoRasterTile with EPSG:4326 or EPSG:2263 must be rejected immediately
        tf_deg = AffineGeotransform(a=0.0001, b=0.0, c=36.8, d=0.0, e=-0.0001, f=-1.2)
        with self.assertRaises(GeodeticMetricCalculationError):
            GeoRasterTile(
                data=np.ones((1, 8, 8), dtype=np.float32),
                transform=tf_deg,
                epsg=4326,
                band_names=("B1",),
                acquisition_date="2026-01-01",
            )
        with self.assertRaises(GeodeticMetricCalculationError):
            GeoRasterTile(
                data=np.ones((1, 8, 8), dtype=np.float32),
                transform=tf_deg,
                epsg=2263,
                band_names=("B1",),
                acquisition_date="2026-01-01",
            )

        # SpatialNetworkEngine with EPSG:2263 or EPSG:4326 must be rejected
        with self.assertRaises(GeodeticMetricCalculationError):
            SpatialNetworkEngine(utm_epsg=2263)
        with self.assertRaises(GeodeticMetricCalculationError):
            SpatialNetworkEngine(utm_epsg=4326)

        # Valid metre CRS (EPSG:32737 and EPSG:21096) succeed
        self.assertFalse(self.crs.assert_projected_crs(32737, require_kenya_epsg=True).is_geographic)
        self.assertFalse(self.crs.assert_projected_crs(21096, require_kenya_epsg=True).is_geographic)

    def test_cross_zone_and_equator_detection(self) -> None:
        flags = self.crs.is_cross_zone_or_equator((35.8, -0.2, 36.3, 0.3))
        self.assertTrue(flags["crosses_utm_zone_boundary_36E"])
        self.assertTrue(flags["crosses_equator_0N"])
        self.assertTrue(flags["requires_geodesic_or_custom_tm"])


class TestAuditCritical3And4And5RasterAndSpectral(unittest.TestCase):
    def test_critical_3_nodata_sentinel_excluded_in_ndvi_and_indices(self) -> None:
        # Exact reproduction from Technical Audit CRITICAL 3:
        nir = np.array([[-9999.0]])
        red = np.array([[-9999.0]])
        result = SpectralIndicesEngine.ndvi(nir, red)
        self.assertFalse(bool(result.valid_mask[0, 0]))
        self.assertTrue(np.isnan(result.values[0, 0]))

        # Mixed valid and NoData array across EVI, MNDWI, NDBI, SAVI, BSI
        nir_m = np.array([[0.45, -9999.0]], dtype=np.float32)
        red_m = np.array([[0.08, -9999.0]], dtype=np.float32)
        blue_m = np.array([[0.04, -9999.0]], dtype=np.float32)
        green_m = np.array([[0.09, -9999.0]], dtype=np.float32)
        swir_m = np.array([[0.18, -9999.0]], dtype=np.float32)

        for res in [
            SpectralIndicesEngine.ndvi(nir_m, red_m),
            SpectralIndicesEngine.evi(nir_m, red_m, blue_m),
            SpectralIndicesEngine.mndwi(green_m, swir_m),
            SpectralIndicesEngine.ndbi(swir_m, nir_m),
            SpectralIndicesEngine.savi(nir_m, red_m),
            SpectralIndicesEngine.bsi(swir_m, red_m, nir_m, blue_m),
        ]:
            self.assertTrue(bool(res.valid_mask[0, 0]))
            self.assertFalse(bool(res.valid_mask[0, 1]))
            self.assertTrue(np.isnan(res.values[0, 1]))

    def test_critical_4_affine_aware_resampling_with_different_origins_and_pixel_sizes(self) -> None:
        # Reference tile: 64x64 at 1.0 m GSD, origin (257500, 9858000)
        ref_tf = AffineGeotransform(a=1.0, b=0.0, c=257_500.0, d=0.0, e=-1.0, f=9_858_000.0)
        ref_data = np.full((3, 64, 64), 0.10, dtype=np.float32)
        # Place a bright target at world UTM box E=[257520..257530], N=[9857970..9857980]
        # In ref_tile: cols 20..30, rows 20..30
        ref_data[0, 20:30, 20:30] = 0.80
        ref_tile = GeoRasterTile(
            data=ref_data,
            transform=ref_tf,
            epsg=32737,
            band_names=("Red", "NIR", "SWIR1"),
            acquisition_date="2025-01-10",
        )

        # Target tile: same array shape (64x64) and same EPSG (32737), but DIFFERENT origin
        # shifted by (+10m East, -10m North) -> origin (257510, 9857990).
        # The same world feature E=[257520..257530], N=[9857970..9857980] lives at cols 10..20, rows 10..20!
        tgt_tf = AffineGeotransform(a=1.0, b=0.0, c=257_510.0, d=0.0, e=-1.0, f=9_857_990.0)
        tgt_data = np.full((3, 64, 64), 0.10, dtype=np.float32)
        tgt_data[0, 10:20, 10:20] = 0.80
        # Also inject a NoData strip at cols 50..64 in target tile
        tgt_data[:, :, 50:64] = -9999.0
        tgt_tile = GeoRasterTile(
            data=tgt_data,
            transform=tgt_tf,
            epsg=32737,
            band_names=("Red", "NIR", "SWIR1"),
            acquisition_date="2026-01-10",
        )

        resampled = RasterProcessingPipeline.resample_tile_to_reference_grid(ref_tile, tgt_tile)
        # In resampled target tile (now on ref_tf grid), the bright feature must appear at rows 20..30, cols 20..30!
        self.assertGreater(float(np.mean(resampled.data[0, 22:28, 22:28])), 0.75)
        self.assertLess(float(np.mean(resampled.data[0, 12:18, 12:18])), 0.15)
        # Columns 0..10 of ref_tile lie WEST of tgt_tile's origin (257510) -> must be masked as NoData!
        self.assertTrue(np.all(resampled.nodata_mask[:, 0:9]))

    def test_critical_5_separation_of_applied_displacement_and_residual_registration_error(self) -> None:
        tile_t1, tile_t2, _ = SyntheticKenyaDatasetGenerator.generate_bitemporal_multiband_tiles(
            height=64, width=64, gsd_m=1.0
        )
        # Shift tile_t2 data by (+3 rows, +4 cols) = 5.0 m displacement
        from scipy.ndimage import shift as nd_shift
        shifted_data = np.array([nd_shift(tile_t1.data[b], shift=(-3.0, -4.0), order=1, mode="nearest") for b in range(5)])
        moving_tile = GeoRasterTile(
            data=shifted_data,
            transform=tile_t1.transform,
            epsg=32737,
            band_names=tile_t1.band_names,
            acquisition_date="2026-02-10",
        )
        align_res = RasterProcessingPipeline.align_temporal_stack(tile_t1, moving_tile)
        # Applied displacement is ~5.0 m (hypot(3, 4) * 1.0 m)
        self.assertAlmostEqual(align_res["applied_displacement_m"], 5.0, delta=0.6)
        # Residual registration error after correction is small (< 0.50 m) and strictly < applied_displacement_m
        self.assertLess(align_res["residual_registration_error_m"], 0.50)
        self.assertLess(align_res["residual_registration_error_m"], align_res["applied_displacement_m"])
        self.assertGreater(align_res["total_positional_uncertainty_1sigma_m"], align_res["residual_registration_error_m"])


class TestAuditSections3To7Enhancements(unittest.TestCase):
    def test_section_3_honest_glitters_grid_naming(self) -> None:
        hex_meta = GlittersHexGridIndex.encode_with_metadata(-1.2921, 36.8219, resolution=9)
        self.assertTrue(hex_meta["glitters_hex_id"].startswith("GHX09_"))
        self.assertEqual(hex_meta["grid_standard"], "GLITTERS_AXIAL_HEX_GRID_V1")

        hilb_meta = GlittersHilbertGridIndex.encode_with_metadata(36.8219, -1.2921, level=16)
        self.assertEqual(hilb_meta["grid_standard"], "GLITTERS_PLANAR_HILBERT_V1")
        self.assertGreater(hilb_meta["glitters_hilbert_id"], 0)

    def test_section_4_cadastral_evidence_required_for_legal_verification(self) -> None:
        poly = box(257500, 9857000, 257530, 9857050)
        # Setting LICENSED_SURVEY_PLAN_REFERENCE + is_legally_verified_cadastre=True WITHOUT evidence must fail
        with self.assertRaises(ValueError):
            ParcelRecord(
                parcel_id="UNVERIFIED_CLAIM",
                geometry_utm=poly,
                utm_epsg=32737,
                observation_date="2026-03-01",
                authority=ObservationAuthority.LICENSED_SURVEY_PLAN_REFERENCE,
                is_legally_verified_cadastre=True,
                cadastral_evidence=None,
            )

        # Supplying valid cryptographic CadastralVerificationEvidence succeeds
        doc_hash = hashlib.sha256(b"Survey of Kenya FR/204/118 Official Plan").hexdigest()
        evidence = CadastralVerificationEvidence(
            survey_plan_reference="FR/204/118",
            issuing_authority="Survey of Kenya",
            authenticated_document_sha256=doc_hash,
            licensed_surveyor_id="LSB-KE-0492",
            verification_timestamp_iso="2026-11-12T10:30:00+03:00",
            verification_status="VERIFIED_AUTHENTIC",
            source_system_reference="ARDHISASA_SURVEY_VERIFY_V1",
        )
        verified_parcel = ParcelRecord(
            parcel_id="VERIFIED_PARCEL_01",
            geometry_utm=poly,
            utm_epsg=32737,
            observation_date="2026-03-01",
            authority=ObservationAuthority.LICENSED_SURVEY_PLAN_REFERENCE,
            is_legally_verified_cadastre=True,
            cadastral_evidence=evidence,
        )
        self.assertTrue(verified_parcel.is_legally_verified_cadastre)

    def test_section_5_postgis_schema_partition_pk_and_rls(self) -> None:
        sql_path = (
            Path(__file__).resolve().parent.parent / "sql" / "postgis_schema.sql"
        )
        sql_text = sql_path.read_text()
        self.assertIn("PRIMARY KEY (parcel_obs_id, local_utm_epsg)", sql_text)
        self.assertIn("21096", sql_text)
        self.assertIn("PRELIMINARY_ALGORITHMIC_SCREENING", sql_text)
        self.assertIn("ENABLE ROW LEVEL SECURITY", sql_text)

    def test_section_6_concave_l_shaped_change_polygon_area_reconciliation(self) -> None:
        # Build a 64x64 tile pair at 1.0 m GSD where an L-shaped building (75 pixels = 75.0 m^2)
        # is constructed inside a 10x10 bounding box (100.0 m^2)
        tf = AffineGeotransform(a=1.0, b=0.0, c=257_500.0, d=0.0, e=-1.0, f=9_858_000.0)
        data_t1 = np.zeros((3, 64, 64), dtype=np.float32)
        data_t1[0] = 0.06  # Red
        data_t1[1] = 0.45  # NIR
        data_t1[2] = 0.15  # SWIR1

        data_t2 = data_t1.copy()
        # L-shape: 10x10 block (r: 20..30, c: 20..30 = 100 px) minus upper-right 5x5 quadrant (r: 20..25, c: 25..30 = 25 px) = 75 px
        l_mask = np.zeros((64, 64), dtype=bool)
        l_mask[20:30, 20:30] = True
        l_mask[20:25, 25:30] = False
        data_t2[0, l_mask] = 0.28
        data_t2[1, l_mask] = 0.14
        data_t2[2, l_mask] = 0.40

        tile_t1 = GeoRasterTile(data_t1, tf, 32737, ("Red", "NIR", "SWIR1"), "2025-02-01")
        tile_t2 = GeoRasterTile(data_t2, tf, 32737, ("Red", "NIR", "SWIR1"), "2026-02-01")

        changes = TemporalChangeDetectionEngine.detect_changes_between_tiles(
            tile_t1, tile_t2, min_change_area_m2=20.0
        )
        self.assertEqual(len(changes), 1)
        chg = changes[0]
        # Exact pixel area AND vector polygon area must both equal 75.0 m^2 (NOT 100.0 m^2 bounding box!)
        self.assertAlmostEqual(chg.pixel_count_area_m2, 75.0, places=3)
        self.assertAlmostEqual(chg.area_m2, 75.0, places=3)
        self.assertAlmostEqual(float(chg.geometry_utm.area), 75.0, places=3)

    def test_section_7_canonical_frechet_sparse_morans_geotiff_and_missing_features(self) -> None:
        geom = ComputationalGeometryEngine()
        # Same rectangle with shifted starting vertex and reversed (CW) vertex order
        p1 = Polygon([(0, 0), (40, 0), (40, 25), (0, 25), (0, 0)])
        p2 = Polygon([(40, 25), (40, 0), (0, 0), (0, 25), (40, 25)])
        cmp_res = geom.compare_boundaries(p1, p2)
        self.assertAlmostEqual(cmp_res.discrete_frechet_distance_m, 0.0, places=4)

        # Sparse Local Moran's I
        pts = np.array([[0.0, 0.0], [10.0, 0.0], [0.0, 10.0], [500.0, 500.0]])
        vals = np.array([10.0, 11.0, 9.5, 1.0])
        m_res = geom.local_morans_i(pts, vals, bandwidth_m=50.0)
        self.assertGreater(m_res["sparse_nnz"], 0)

        # Binary GeoTIFF writer
        tf = AffineGeotransform(a=10.0, b=0.0, c=257_500.0, d=0.0, e=-10.0, f=9_858_000.0)
        tile = GeoRasterTile(np.ones((1, 16, 16), dtype=np.float32), tf, 32737, ("B1",), "2026-01-01")
        with tempfile.TemporaryDirectory() as tmpd:
            tif_p = RasterProcessingPipeline.write_geotiff_cog_file(tile, Path(tmpd) / "test_cog.tif")
            raw_bytes = tif_p.read_bytes()
            self.assertEqual(raw_bytes[:2], b"II")
            magic_42 = struct.unpack("<H", raw_bytes[2:4])[0]
            self.assertEqual(magic_42, 42)

        # Terrain NoData propagation
        dem = SyntheticKenyaDatasetGenerator.generate_escarpment_valley_dem(rows=32, cols=32)
        dem[5, 5] = -9999.0
        t_res = TerrainAndHydrologyEngine.analyze_terrain(dem, cell_size_m=30.0, nodata_value=-9999.0)
        self.assertFalse(bool(t_res.valid_mask[5, 5]))
        self.assertTrue(np.isnan(t_res.slope_deg[5, 5]))
        self.assertTrue(np.isnan(t_res.flood_susceptibility_score[5, 5]))

        # Missing feature handling (zero silent default imputation)
        syn = SyntheticKenyaDatasetGenerator.generate_nairobi_parcel_fabric(n_rows=2, n_cols=2, inject_anomalies=False)
        net, lm = SyntheticKenyaDatasetGenerator.generate_road_network_graph(grid_size=4)
        extractor = PropertySpatialIntelligenceExtractor()
        fv = extractor.extract_feature_vector(
            property_id="PROP_MISSING_TEST",
            parcel=syn["parcels"][0],
            admin_tag=KenyaAdminHierarchyTag(county_code=47, county_name="Nairobi"),
            building_footprints_utm=[],
            surrounding_parcels_utm=[],
            network_engine=net,
            arterial_node_id=lm["arterial_node"],
            hospital_node_id=lm["hospital_node"],
            school_node_id=lm["school_node"],
            utility_distance_m=None,
            terrain_summary=None,
            land_cover_summary=None,
        )
        fv_dict = fv.to_dict()
        self.assertIsNone(fv_dict["features"]["elevation_mean_m"])
        self.assertIsNone(fv_dict["features"]["topographic_wetness_index_mean"])
        self.assertIsNone(fv_dict["features"]["preliminary_flood_susceptibility_score"])
        self.assertLess(fv.completeness_ratio, 1.0)
        self.assertIn("elevation_mean_m", fv.missing_feature_names)


class TestOriginalAdversarialAndPropertySuite(unittest.TestCase):
    def setUp(self) -> None:
        self.geom = ComputationalGeometryEngine(grid_size_m=0.001)
        self.parcel_engine = ParcelIntelligenceEngine()

    def test_point_in_polygon_inside_boundary_outside_and_hole(self) -> None:
        exterior = [(0, 0), (100, 0), (100, 100), (0, 100), (0, 0)]
        hole = [(30, 30), (70, 30), (70, 70), (30, 70), (30, 30)]
        poly = Polygon(exterior, [hole])
        self.assertEqual(self.geom.point_in_polygon(Point(15, 15), poly)["classification"], "INSIDE")
        self.assertEqual(self.geom.point_in_polygon(Point(100, 50), poly)["classification"], "BOUNDARY")
        self.assertEqual(self.geom.point_in_polygon(Point(50, 50), poly)["classification"], "OUTSIDE")

    def test_property_based_boolean_overlay_invariants(self) -> None:
        rng = np.random.default_rng(12345)
        for _ in range(30):
            x1, y1 = rng.uniform(250_000, 251_000, size=2)
            w1, h1 = rng.uniform(20, 120, size=2)
            dx, dy = rng.uniform(-50, 50, size=2)
            w2, h2 = rng.uniform(20, 120, size=2)

            poly_a = box(x1, y1, x1 + w1, y1 + h1)
            poly_b = box(x1 + dx, y1 + dy, x1 + dx + w2, y1 + dy + h2)

            inter_ab = self.geom.polygon_intersection(poly_a, poly_b)
            inter_ba = self.geom.polygon_intersection(poly_b, poly_a)
            union_ab = self.geom.polygon_union(poly_a, poly_b)
            diff_ab = self.geom.polygon_difference(poly_a, poly_b)

            self.assertAlmostEqual(inter_ab.area, inter_ba.area, places=2)
            self.assertAlmostEqual(union_ab.area, poly_a.area + poly_b.area - inter_ab.area, delta=0.5)
            self.assertAlmostEqual(diff_ab.area + inter_ab.area, poly_a.area, delta=0.5)

    def test_adversarial_self_intersecting_bowtie_polygon_repair(self) -> None:
        bowtie = Polygon([(0, 0), (100, 100), (100, 0), (0, 100), (0, 0)])
        report = self.geom.inspect_and_repair_validity(bowtie)
        self.assertFalse(report.is_valid)
        self.assertTrue(report.has_self_intersection)
        self.assertTrue(report.repaired_geometry.is_valid)
        self.assertAlmostEqual(report.repaired_geometry.area, 5_000.0, places=2)

    def test_synthetic_nairobi_parcel_anomalies_and_lineage(self) -> None:
        syn = SyntheticKenyaDatasetGenerator.generate_nairobi_parcel_fabric(
            n_rows=4, n_cols=4, inject_anomalies=True
        )
        parcels = syn["parcels"]
        polys = [p.geometry_utm for p in parcels]
        ids = [p.parcel_id for p in parcels]

        overlaps = self.parcel_engine.topo_engine.detect_overlaps(ids, polys)
        self.assertGreaterEqual(len(overlaps), 2)
        gaps = self.parcel_engine.topo_engine.detect_interstitial_gaps(ids, polys, max_gap_width_m=2.0)
        self.assertGreaterEqual(len(gaps), 1)
        dups = self.parcel_engine.detect_duplicate_parcels(parcels)
        self.assertEqual(len(dups), 1)

        parent = ParcelRecord("PARENT_01", box(0, 0, 100, 100), 32737, "2024-01-01")
        children = [
            ParcelRecord("CHILD_01", box(0, 0, 45, 50), 32737, "2026-01-01"),
            ParcelRecord("CHILD_02", box(0, 50, 45, 100), 32737, "2026-01-01"),
            ParcelRecord("CHILD_03", box(55, 0, 100, 50), 32737, "2026-01-01"),
            ParcelRecord("CHILD_04", box(55, 50, 100, 100), 32737, "2026-01-01"),
        ]
        events = self.parcel_engine.infer_subdivision_and_consolidation([parent], children)
        self.assertEqual(len(events), 1)
        self.assertTrue(events[0].is_exclusive_assignment)
        self.assertEqual(events[0].lineage_authority, "INFERRED_CANDIDATE_RELATIONSHIP_ONLY")

    def test_resolution_gatekeeper_rejects_coarse_building_extraction(self) -> None:
        with self.assertRaises(InsufficientSpatialResolutionError):
            BuildingAndLandDetectionEngine.check_resolution_sufficiency(
                gsd_m=10.0, task="INDIVIDUAL_BUILDING_FOOTPRINT"
            )

    def test_network_routing_and_corridor_isochrone(self) -> None:
        net, _ = SyntheticKenyaDatasetGenerator.generate_road_network_graph(grid_size=6)
        r_dijk = net.shortest_path_dijkstra(0, 35)
        r_astar = net.shortest_path_astar(0, 35)
        ch_cost = net.query_contraction_hierarchy(0, 35)
        self.assertAlmostEqual(r_dijk.travel_time_sec, r_astar.travel_time_sec, places=4)
        self.assertAlmostEqual(r_dijk.travel_time_sec, ch_cost, places=4)

        iso = net.compute_isochrone_polygon(0, max_travel_time_sec=60.0, buffer_m=40.0)
        self.assertGreater(iso.area, 0.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
