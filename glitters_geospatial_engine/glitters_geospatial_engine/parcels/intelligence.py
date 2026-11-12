"""
Parcel Intelligence Engine (`parcels/intelligence.py` v1.1.0)
=============================================================

Upgrades in v1.1.0:
1. Cryptographic & structured `CadastralVerificationEvidence` required whenever
   `is_legally_verified_cadastre=True`. Setting the enum and boolean without valid
   authenticated survey plan metadata and a 64-hex SHA-256 document hash is rejected.
2. Enforces `KenyaCRSEngine.assert_projected_crs(self.utm_epsg, require_kenya_epsg=True)`
   at `ParcelRecord` construction.
3. Subdivision and consolidation inference emits candidate relationships with explicit
   `is_exclusive_assignment` flags and supporting intersection geometry, never asserting
   authoritative legal subdivision without survey records.
"""

from __future__ import annotations
from dataclasses import dataclass, field
import hashlib
import hmac
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple
from shapely.geometry import Polygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union
from shapely.strtree import STRtree

from ..crs.kenya_crs import KenyaCRSEngine, ObservationAuthority
from ..geometry.algorithms import ComputationalGeometryEngine
from ..topology.planar_graph import TopologyEngine


_SHA256_HEX_RE = re.compile(r"^[0-9a-fA-F]{64}$")


@dataclass(frozen=True)
class CadastralVerificationEvidence:
    """
    Mandatory cryptographic and documentary evidence required before any parcel record
    or GLITTERS CORE contract can set `is_legally_verified_cadastre = True`.
    """
    survey_plan_reference: str          # e.g. "FR/204/118" or RIM sheet reference
    issuing_authority: str              # e.g. "Survey of Kenya / Ministry of Lands"
    authenticated_document_sha256: str  # 64-hex SHA-256 hash of the signed survey plan
    licensed_surveyor_id: str           # Land Surveyors Board (LSB) / ISK license number
    verification_timestamp_iso: str     # ISO-8601 verification timestamp
    verification_status: str            # Must be "VERIFIED_AUTHENTIC"
    source_system_reference: str        # e.g. "ARDHISASA_SURVEY_VERIFY_V1"

    def validate(self) -> bool:
        if not self.survey_plan_reference or len(self.survey_plan_reference.strip()) < 4:
            raise ValueError("CadastralVerificationEvidence requires a non-empty `survey_plan_reference`.")
        if not self.issuing_authority or len(self.issuing_authority.strip()) < 4:
            raise ValueError("CadastralVerificationEvidence requires a valid `issuing_authority`.")
        if not _SHA256_HEX_RE.match(self.authenticated_document_sha256 or ""):
            raise ValueError(
                "CadastralVerificationEvidence requires a valid 64-character hexadecimal SHA-256 "
                "`authenticated_document_sha256` digest of the survey plan."
            )
        if not self.licensed_surveyor_id or len(self.licensed_surveyor_id.strip()) < 3:
            raise ValueError("CadastralVerificationEvidence requires a `licensed_surveyor_id`.")
        if not self.verification_timestamp_iso or "T" not in self.verification_timestamp_iso:
            raise ValueError("CadastralVerificationEvidence requires an ISO-8601 `verification_timestamp_iso`.")
        if self.verification_status != "VERIFIED_AUTHENTIC":
            raise ValueError(
                f"CadastralVerificationEvidence `verification_status` must be 'VERIFIED_AUTHENTIC', "
                f"got '{self.verification_status}'."
            )
        if not self.source_system_reference or len(self.source_system_reference.strip()) < 3:
            raise ValueError("CadastralVerificationEvidence requires a `source_system_reference`.")
        return True

    def compute_evidence_digest(self) -> str:
        payload = (
            f"{self.survey_plan_reference}|{self.issuing_authority}|"
            f"{self.authenticated_document_sha256.lower()}|{self.licensed_surveyor_id}|"
            f"{self.verification_timestamp_iso}|{self.verification_status}|{self.source_system_reference}"
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ParcelRecord:
    """
    Standardized representation of a land parcel geometry with strict CRS validation
    and cryptographic cadastral evidence verification.
    """
    parcel_id: str
    geometry_utm: Polygon
    utm_epsg: int
    observation_date: str
    authority: ObservationAuthority = ObservationAuthority.INFERRED_GEOMETRIC_BOUNDARY
    is_legally_verified_cadastre: bool = False
    cadastral_evidence: Optional[CadastralVerificationEvidence] = None
    stated_deed_area_m2: Optional[float] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # 1. Enforce metric Kenya UTM CRS on every parcel geometry
        KenyaCRSEngine.assert_projected_crs(self.utm_epsg, require_kenya_epsg=True)

        # 2. Enforce strict cadastral authority & documentary evidence check
        if self.is_legally_verified_cadastre:
            if self.authority != ObservationAuthority.LICENSED_SURVEY_PLAN_REFERENCE:
                raise ValueError(
                    "Non-negotiable invariant violation: A parcel with authority "
                    f"'{self.authority.value}' cannot be flagged as a legally verified cadastral boundary."
                )
            if self.cadastral_evidence is None:
                raise ValueError(
                    "Non-negotiable invariant violation: Setting `is_legally_verified_cadastre=True` "
                    "requires an authenticated `CadastralVerificationEvidence` instance (survey plan ref, "
                    "issuing authority, SHA-256 document hash, surveyor license ID, and timestamp)."
                )
            self.cadastral_evidence.validate()


@dataclass(frozen=True)
class LineageEvent:
    """
    Inferred candidate parcel subdivision (1 -> N) or consolidation (M -> 1) relationship.
    Explicitly marked as a candidate spatial relationship with exclusivity diagnostics and
    supporting overlap geometry, never an authoritative legal mutation record.
    """
    event_type: str  # 'CANDIDATE_SUBDIVISION', 'CANDIDATE_CONSOLIDATION'
    parent_parcel_ids: Tuple[str, ...]
    child_parcel_ids: Tuple[str, ...]
    parent_total_area_m2: float
    child_total_area_m2: float
    implied_road_reserve_or_loss_m2: float
    spatial_overlap_ratio: float
    confidence: float
    is_exclusive_assignment: bool = True
    lineage_authority: str = "INFERRED_CANDIDATE_RELATIONSHIP_ONLY"
    supporting_intersection_geometry: Optional[BaseGeometry] = None


class ParcelIntelligenceEngine:
    """
    Comprehensive algorithmic suite for analysing parcel geometries, topological anomalies,
    duplicate records, suspicious boundary mutations, and candidate subdivision/consolidation lineage.
    """

    def __init__(self, grid_size_m: float = 0.001) -> None:
        self.crs_engine = KenyaCRSEngine()
        self.geom_engine = ComputationalGeometryEngine(grid_size_m=grid_size_m)
        self.topo_engine = TopologyEngine()

    def calculate_parcel_metrics(self, parcel: ParcelRecord) -> Dict[str, Any]:
        self.crs_engine.assert_projected_crs(parcel.utm_epsg, require_kenya_epsg=True)
        geom_wgs84 = self.crs_engine.transform_geometry(parcel.geometry_utm, parcel.utm_epsg, 4326)
        geod_stats = self.crs_engine.calculate_geodesic_area_and_perimeter(geom_wgs84)
        validity = self.geom_engine.inspect_and_repair_validity(parcel.geometry_utm)

        utm_area = float(parcel.geometry_utm.area)
        utm_perim = float(parcel.geometry_utm.length)

        deed_discrepancy_pct = None
        if parcel.stated_deed_area_m2 and parcel.stated_deed_area_m2 > 0:
            deed_discrepancy_pct = (
                (utm_area - parcel.stated_deed_area_m2) / parcel.stated_deed_area_m2 * 100.0
            )

        return {
            "parcel_id": parcel.parcel_id,
            "utm_epsg": parcel.utm_epsg,
            "authority": parcel.authority.value,
            "is_legally_verified_cadastre": parcel.is_legally_verified_cadastre,
            "cadastral_evidence_digest": (
                parcel.cadastral_evidence.compute_evidence_digest()
                if parcel.cadastral_evidence is not None
                else None
            ),
            "utm_area_m2": utm_area,
            "utm_area_hectares": utm_area / 10_000.0,
            "utm_area_acres": utm_area / 4_046.8564224,
            "utm_perimeter_m": utm_perim,
            "geodesic_area_m2": geod_stats["geodesic_area_m2"],
            "geodesic_perimeter_m": geod_stats["geodesic_perimeter_m"],
            "polsby_popper_compactness": validity.polsby_popper_compactness,
            "is_geometrically_valid": validity.is_valid,
            "stated_deed_area_m2": parcel.stated_deed_area_m2,
            "deed_area_discrepancy_pct": deed_discrepancy_pct,
        }

    def detect_duplicate_parcels(
        self,
        parcels: Sequence[ParcelRecord],
        iou_threshold: float = 0.96,
        max_hausdorff_m: float = 0.50,
    ) -> List[Dict[str, Any]]:
        polys = [p.geometry_utm for p in parcels]
        tree = STRtree(polys)
        duplicates: List[Dict[str, Any]] = []
        seen = set()

        for i, p_i in enumerate(parcels):
            cands = tree.query(p_i.geometry_utm, predicate="intersects")
            for j in cands:
                j = int(j)
                if i >= j:
                    continue
                key = (min(i, j), max(i, j))
                if key in seen:
                    continue
                seen.add(key)

                metrics = self.geom_engine.compare_boundaries(
                    p_i.geometry_utm, parcels[j].geometry_utm
                )
                if (
                    metrics.intersection_over_union >= iou_threshold
                    and metrics.hausdorff_distance_m <= max_hausdorff_m
                ):
                    duplicates.append(
                        {
                            "parcel_id_a": p_i.parcel_id,
                            "parcel_id_b": parcels[j].parcel_id,
                            "iou": metrics.intersection_over_union,
                            "hausdorff_distance_m": metrics.hausdorff_distance_m,
                            "centroid_shift_m": metrics.centroid_displacement_m,
                        }
                    )
        return duplicates

    def detect_geometrically_inconsistent_parcels(
        self, parcels: Sequence[ParcelRecord]
    ) -> List[Dict[str, Any]]:
        flagged: List[Dict[str, Any]] = []
        for p in parcels:
            validity = self.geom_engine.inspect_and_repair_validity(p.geometry_utm)
            metrics = self.calculate_parcel_metrics(p)
            reasons: List[str] = []

            if not validity.is_valid:
                reasons.append(f"Invalid topology: {validity.validity_reason}")
            if validity.polsby_popper_compactness < 0.06:
                reasons.append(
                    f"Extreme sliver/corridor geometry (Polsby-Popper={validity.polsby_popper_compactness:.4f})"
                )
            if (
                validity.minimum_clearance_m > 0
                and validity.minimum_clearance_m < 0.10
            ):
                reasons.append(
                    f"Sub-decimeter pinch/spike clearance ({validity.minimum_clearance_m:.4f} m)"
                )
            if (
                metrics["deed_area_discrepancy_pct"] is not None
                and abs(metrics["deed_area_discrepancy_pct"]) > 5.0
            ):
                reasons.append(
                    f"Deed area discrepancy of {metrics['deed_area_discrepancy_pct']:+.2f}%"
                )

            if reasons:
                flagged.append(
                    {
                        "parcel_id": p.parcel_id,
                        "reasons": reasons,
                        "validity_report": validity,
                        "metrics": metrics,
                    }
                )
        return flagged

    def detect_suspicious_boundary_changes(
        self,
        historical_parcel: ParcelRecord,
        current_parcel: ParcelRecord,
        survey_tolerance_m: float = 0.30,
        max_allowable_area_drift_pct: float = 1.5,
    ) -> Dict[str, Any]:
        if historical_parcel.utm_epsg != current_parcel.utm_epsg:
            raise ValueError("Historical and current parcels must share the same UTM EPSG code.")

        cmp_metrics = self.geom_engine.compare_boundaries(
            historical_parcel.geometry_utm, current_parcel.geometry_utm
        )
        area_drift_pct = cmp_metrics.area_relative_change * 100.0
        encroachment_geom = self.geom_engine.polygon_difference(
            current_parcel.geometry_utm,
            historical_parcel.geometry_utm.buffer(survey_tolerance_m),
        )
        encroachment_area_m2 = float(encroachment_geom.area)

        anomalies: List[str] = []
        if abs(area_drift_pct) > max_allowable_area_drift_pct:
            anomalies.append(f"Area changed by {area_drift_pct:+.2f}% (exceeds ±{max_allowable_area_drift_pct}%)")
        if cmp_metrics.hausdorff_distance_m > survey_tolerance_m:
            anomalies.append(
                f"Maximum boundary displacement {cmp_metrics.hausdorff_distance_m:.2f} m exceeds tolerance {survey_tolerance_m:.2f} m"
            )
        if encroachment_area_m2 > 1.0:
            anomalies.append(
                f"Outward boundary creep of {encroachment_area_m2:.2f} m² beyond survey tolerance buffer"
            )

        return {
            "parcel_id": current_parcel.parcel_id,
            "is_suspicious": len(anomalies) > 0,
            "anomalies": anomalies,
            "area_drift_pct": area_drift_pct,
            "hausdorff_displacement_m": cmp_metrics.hausdorff_distance_m,
            "discrete_frechet_m": cmp_metrics.discrete_frechet_distance_m,
            "outward_encroachment_area_m2": encroachment_area_m2,
            "iou": cmp_metrics.intersection_over_union,
            "legal_disclaimer": (
                "Algorithmic boundary comparison is an intelligence screening signal and "
                "does not constitute an official cadastral mutation determination."
            ),
        }

    def infer_subdivision_and_consolidation(
        self,
        epoch_t1_parcels: Sequence[ParcelRecord],
        epoch_t2_parcels: Sequence[ParcelRecord],
        min_containment_ratio: float = 0.70,
    ) -> List[LineageEvent]:
        """
        Reconstruct a bipartite spatial candidate lineage graph between epoch T1 and epoch T2,
        tracking global parent/child assignment exclusivity so overlapping/ambiguous records
        are flagged (`is_exclusive_assignment = False`) and supporting intersection geometries
        are preserved.
        """
        t1_polys = [p.geometry_utm for p in epoch_t1_parcels]
        t2_polys = [p.geometry_utm for p in epoch_t2_parcels]
        tree_t2 = STRtree(t2_polys)
        tree_t1 = STRtree(t1_polys)

        child_claim_counts: Dict[str, int] = {}
        parent_claim_counts: Dict[str, int] = {}
        raw_subdivisions: List[Dict[str, Any]] = []
        raw_consolidations: List[Dict[str, Any]] = []

        # 1. Candidate Subdivisions: 1 parent in T1 -> multiple children in T2
        for parent in epoch_t1_parcels:
            cand_t2 = tree_t2.query(parent.geometry_utm, predicate="intersects")
            matched_children: List[ParcelRecord] = []
            inter_geoms: List[BaseGeometry] = []
            inter_sum = 0.0
            for j in cand_t2:
                child = epoch_t2_parcels[int(j)]
                inter = self.geom_engine.polygon_intersection(parent.geometry_utm, child.geometry_utm)
                if inter.area / max(child.geometry_utm.area, 1e-9) >= min_containment_ratio:
                    matched_children.append(child)
                    inter_geoms.append(inter)
                    inter_sum += float(inter.area)

            if len(matched_children) >= 2:
                for c in matched_children:
                    child_claim_counts[c.parcel_id] = child_claim_counts.get(c.parcel_id, 0) + 1
                parent_claim_counts[parent.parcel_id] = parent_claim_counts.get(parent.parcel_id, 0) + 1
                raw_subdivisions.append(
                    {
                        "parent": parent,
                        "children": matched_children,
                        "inter_sum": inter_sum,
                        "support_geom": unary_union(inter_geoms),
                    }
                )

        # 2. Candidate Consolidations: M >= 2 parents in T1 -> 1 child in T2
        for child in epoch_t2_parcels:
            cand_t1 = tree_t1.query(child.geometry_utm, predicate="intersects")
            matched_parents: List[ParcelRecord] = []
            inter_geoms = []
            inter_sum = 0.0
            for i in cand_t1:
                parent = epoch_t1_parcels[int(i)]
                inter = self.geom_engine.polygon_intersection(parent.geometry_utm, child.geometry_utm)
                if inter.area / max(parent.geometry_utm.area, 1e-9) >= min_containment_ratio:
                    matched_parents.append(parent)
                    inter_geoms.append(inter)
                    inter_sum += float(inter.area)

            if len(matched_parents) >= 2:
                for p in matched_parents:
                    parent_claim_counts[p.parcel_id] = parent_claim_counts.get(p.parcel_id, 0) + 1
                child_claim_counts[child.parcel_id] = child_claim_counts.get(child.parcel_id, 0) + 1
                raw_consolidations.append(
                    {
                        "parents": matched_parents,
                        "child": child,
                        "inter_sum": inter_sum,
                        "support_geom": unary_union(inter_geoms),
                    }
                )

        events: List[LineageEvent] = []
        for sub in raw_subdivisions:
            parent = sub["parent"]
            children = sub["children"]
            parent_area = float(parent.geometry_utm.area)
            child_area_sum = sum(float(c.geometry_utm.area) for c in children)
            road_reserve_m2 = max(0.0, parent_area - child_area_sum)
            overlap_ratio = min(1.0, sub["inter_sum"] / max(parent_area, 1e-9))
            is_excl = (
                parent_claim_counts.get(parent.parcel_id, 0) == 1
                and all(child_claim_counts.get(c.parcel_id, 0) == 1 for c in children)
            )
            conf = min(0.99, (0.5 + 0.5 * overlap_ratio) * (1.0 if is_excl else 0.70))
            events.append(
                LineageEvent(
                    event_type="SUBDIVISION",
                    parent_parcel_ids=(parent.parcel_id,),
                    child_parcel_ids=tuple(c.parcel_id for c in children),
                    parent_total_area_m2=parent_area,
                    child_total_area_m2=child_area_sum,
                    implied_road_reserve_or_loss_m2=road_reserve_m2,
                    spatial_overlap_ratio=overlap_ratio,
                    confidence=conf,
                    is_exclusive_assignment=is_excl,
                    lineage_authority="INFERRED_CANDIDATE_RELATIONSHIP_ONLY",
                    supporting_intersection_geometry=sub["support_geom"],
                )
            )

        for con in raw_consolidations:
            parents = con["parents"]
            child = con["child"]
            parent_area_sum = sum(float(p.geometry_utm.area) for p in parents)
            child_area = float(child.geometry_utm.area)
            overlap_ratio = min(1.0, con["inter_sum"] / max(child_area, 1e-9))
            is_excl = (
                child_claim_counts.get(child.parcel_id, 0) == 1
                and all(parent_claim_counts.get(p.parcel_id, 0) == 1 for p in parents)
            )
            conf = min(0.99, (0.5 + 0.5 * overlap_ratio) * (1.0 if is_excl else 0.70))
            events.append(
                LineageEvent(
                    event_type="CONSOLIDATION",
                    parent_parcel_ids=tuple(p.parcel_id for p in parents),
                    child_parcel_ids=(child.parcel_id,),
                    parent_total_area_m2=parent_area_sum,
                    child_total_area_m2=child_area,
                    implied_road_reserve_or_loss_m2=max(0.0, parent_area_sum - child_area),
                    spatial_overlap_ratio=overlap_ratio,
                    confidence=conf,
                    is_exclusive_assignment=is_excl,
                    lineage_authority="INFERRED_CANDIDATE_RELATIONSHIP_ONLY",
                    supporting_intersection_geometry=con["support_geom"],
                )
            )

        return events
