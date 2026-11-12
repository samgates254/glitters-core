"""
Computational Quantity Takeoff (QTO) Engine with Cryptographic Measurement Evidence Gate
and Strict Input-Geometry vs. Derived-Material Provenance Separation.
"""
from __future__ import annotations
import math
from typing import Dict, Optional, Tuple
from ..project_model import MeasurementStatus
from .evidence import SignedMeasurementEvidence, TakeoffResult
from .units import UNIT_DIMENSIONS, convert_units_dimensionally

CONCRETE_MIX_DESIGN_PER_M3: Dict[str, Dict[str, float]] = {
    "C15/20 (1:3:6)": {"cement_50kg_bags": 4.4, "sand_m3": 0.47, "aggregate_m3": 0.94, "water_litres": 180.0},
    "C20/25 (1:2:4)": {"cement_50kg_bags": 6.4, "sand_m3": 0.45, "aggregate_m3": 0.90, "water_litres": 185.0},
    "C25/30 (1:1.5:3)": {"cement_50kg_bags": 7.8, "sand_m3": 0.42, "aggregate_m3": 0.84, "water_litres": 190.0},
    "C30/37 (1:1:2)": {"cement_50kg_bags": 9.8, "sand_m3": 0.36, "aggregate_m3": 0.72, "water_litres": 195.0},
}

DEFAULT_WASTE_ALLOWANCE: Dict[str, float] = {
    "concrete": 0.05,
    "reinforcement_steel": 0.075,
    "masonry_stone": 0.06,
    "mortar": 0.10,
    "timber_formwork": 0.12,
    "roofing_sheets": 0.08,
    "floor_tiles": 0.07,
    "plaster": 0.10,
    "paint": 0.05,
    "electrical_cable": 0.06,
    "plumbing_pipe": 0.05,
}


def _resolve_evidence_provenance(
    measurement_evidence: Optional[SignedMeasurementEvidence],
    is_verified_measurement: Optional[bool] = None,
) -> Tuple[MeasurementStatus, MeasurementStatus, Optional[str]]:
    """
    Remediates CRITICAL 2:
    - If a caller passes `is_verified_measurement=True` without a valid `SignedMeasurementEvidence`,
      raises ValueError immediately! Self-declared measurement booleans are strictly prohibited.
    - When `measurement_evidence` is supplied, verifies its cryptographic signature and surveyor registration.
    - Even when `input_geometry_status` is VERIFIED_MEASUREMENT, `derived_material_status` is ALWAYS
      CALCULATED_ESTIMATE because constituent materials depend on mix/waste/bulking models.
    """
    if is_verified_measurement is True and measurement_evidence is None:
        raise ValueError(
            "CRITICAL 2 Security Gate: Self-declared `is_verified_measurement=True` is prohibited. "
            "Supply a verifiable `SignedMeasurementEvidence` record."
        )
    if measurement_evidence is not None:
        ok, errs = measurement_evidence.verify_evidence()
        if not ok:
            raise ValueError(f"Invalid SignedMeasurementEvidence: {'; '.join(errs)}")
        return (
            MeasurementStatus.VERIFIED_MEASUREMENT,
            MeasurementStatus.CALCULATED_ESTIMATE,
            measurement_evidence.measurement_id,
        )
    return (
        MeasurementStatus.CALCULATED_ESTIMATE,
        MeasurementStatus.CALCULATED_ESTIMATE,
        None,
    )


class QuantityTakeoffEngine:
    """
    Mathematical Quantity Takeoff Engine with strict measurement evidence verification
    and dimensional unit validation.
    """

    @staticmethod
    def convert_units(value: float, from_unit: str, to_unit: str) -> float:
        return convert_units_dimensionally(value, from_unit, to_unit)

    @staticmethod
    def estimate_excavation(
        length_m: float,
        width_m: float,
        depth_m: float,
        working_space_m: float = 0.25,
        bulking_factor: float = 1.25,
        measurement_evidence: Optional[SignedMeasurementEvidence] = None,
        is_verified_measurement: Optional[bool] = None,
    ) -> TakeoffResult:
        if min(length_m, width_m, depth_m) <= 0:
            raise ValueError("Excavation dimensions must be strictly positive.")
        geom_status, derived_status, ev_id = _resolve_evidence_provenance(
            measurement_evidence, is_verified_measurement
        )
        eff_l = length_m + 2.0 * working_space_m
        eff_w = width_m + 2.0 * working_space_m
        bank_volume_m3 = eff_l * eff_w * depth_m
        loose_volume_m3 = bank_volume_m3 * bulking_factor
        return TakeoffResult(
            component_type="Excavation",
            net_quantity=round(bank_volume_m3, 4),
            unit="m3",
            waste_factor=round(bulking_factor - 1.0, 4),
            gross_quantity=round(loose_volume_m3, 4),
            material_breakdown={
                "bank_volume_m3": round(bank_volume_m3, 4),
                "loose_haulage_volume_m3": round(loose_volume_m3, 4),
            },
            measurement_status=geom_status,
            input_geometry_status=geom_status,
            derived_material_status=derived_status,
            measurement_evidence_id=ev_id,
            formula_reference="V_bank = (L + 2*w_s)*(W + 2*w_s)*D; V_loose = V_bank * B_f",
        )

    @staticmethod
    def estimate_concrete_works(
        volume_m3: float,
        concrete_class: str = "C25/30 (1:1.5:3)",
        waste_factor: Optional[float] = None,
        component_label: str = "Concrete Works",
        measurement_evidence: Optional[SignedMeasurementEvidence] = None,
        is_verified_measurement: Optional[bool] = None,
    ) -> TakeoffResult:
        if volume_m3 <= 0:
            raise ValueError("Concrete volume must be positive.")
        if concrete_class not in CONCRETE_MIX_DESIGN_PER_M3:
            raise ValueError(f"Unsupported concrete class '{concrete_class}'")
        geom_status, derived_status, ev_id = _resolve_evidence_provenance(
            measurement_evidence, is_verified_measurement
        )
        w = DEFAULT_WASTE_ALLOWANCE["concrete"] if waste_factor is None else waste_factor
        gross_vol = volume_m3 * (1.0 + w)
        mix = CONCRETE_MIX_DESIGN_PER_M3[concrete_class]
        materials = {
            "cement_50kg_bags": round(gross_vol * mix["cement_50kg_bags"], 2),
            "sand_m3": round(gross_vol * mix["sand_m3"], 3),
            "aggregate_m3": round(gross_vol * mix["aggregate_m3"], 3),
            "water_litres": round(gross_vol * mix["water_litres"], 1),
        }
        return TakeoffResult(
            component_type=component_label,
            net_quantity=round(volume_m3, 4),
            unit="m3",
            waste_factor=w,
            gross_quantity=round(gross_vol, 4),
            material_breakdown=materials,
            measurement_status=geom_status,
            input_geometry_status=geom_status,
            derived_material_status=derived_status,
            measurement_evidence_id=ev_id,
            formula_reference=f"Q_gross = V_net * (1 + {w}); Dry factor = 1.54 ({concrete_class})",
        )

    @staticmethod
    def estimate_foundation(
        pad_count: int,
        pad_length_m: float,
        pad_width_m: float,
        pad_thickness_m: float,
        blinding_thickness_m: float = 0.05,
        concrete_class: str = "C25/30 (1:1.5:3)",
        measurement_evidence: Optional[SignedMeasurementEvidence] = None,
        is_verified_measurement: Optional[bool] = None,
    ) -> TakeoffResult:
        net_pad_vol = pad_count * pad_length_m * pad_width_m * pad_thickness_m
        blinding_vol = pad_count * pad_length_m * pad_width_m * blinding_thickness_m
        base_res = QuantityTakeoffEngine.estimate_concrete_works(
            volume_m3=net_pad_vol,
            concrete_class=concrete_class,
            component_label="Foundations",
            measurement_evidence=measurement_evidence,
            is_verified_measurement=is_verified_measurement,
        )
        blinding_res = QuantityTakeoffEngine.estimate_concrete_works(
            volume_m3=blinding_vol,
            concrete_class="C15/20 (1:3:6)",
            component_label="Foundation Blinding",
            measurement_evidence=measurement_evidence,
            is_verified_measurement=is_verified_measurement,
        )
        combined = dict(base_res.material_breakdown)
        combined["blinding_c15_m3"] = blinding_res.gross_quantity
        combined["cement_50kg_bags"] = round(
            combined["cement_50kg_bags"] + blinding_res.material_breakdown["cement_50kg_bags"], 2
        )
        return TakeoffResult(
            component_type="Foundations",
            net_quantity=round(net_pad_vol, 4),
            unit="m3",
            waste_factor=base_res.waste_factor,
            gross_quantity=base_res.gross_quantity,
            material_breakdown=combined,
            measurement_status=base_res.measurement_status,
            input_geometry_status=base_res.input_geometry_status,
            derived_material_status=MeasurementStatus.CALCULATED_ESTIMATE,
            measurement_evidence_id=base_res.measurement_evidence_id,
            formula_reference="V_pad = N * L * W * t_pad; V_blind = N * L * W * t_blind",
        )

    @staticmethod
    def estimate_reinforcement(
        bar_diameter_mm: float,
        total_bar_length_m: float,
        lap_factor: float = 0.08,
        waste_factor: Optional[float] = None,
        measurement_evidence: Optional[SignedMeasurementEvidence] = None,
        is_verified_measurement: Optional[bool] = None,
    ) -> TakeoffResult:
        if bar_diameter_mm <= 0 or total_bar_length_m <= 0:
            raise ValueError("Bar diameter and length must be positive.")
        geom_status, derived_status, ev_id = _resolve_evidence_provenance(
            measurement_evidence, is_verified_measurement
        )
        unit_weight_kg_per_m = (bar_diameter_mm ** 2) / 162.2
        effective_length_m = total_bar_length_m * (1.0 + lap_factor)
        net_mass_kg = effective_length_m * unit_weight_kg_per_m
        w = DEFAULT_WASTE_ALLOWANCE["reinforcement_steel"] if waste_factor is None else waste_factor
        gross_mass_kg = net_mass_kg * (1.0 + w)
        binding_wire_kg = (gross_mass_kg / 1000.0) * 12.0
        return TakeoffResult(
            component_type="Reinforcement",
            net_quantity=round(net_mass_kg, 2),
            unit="kg",
            waste_factor=w,
            gross_quantity=round(gross_mass_kg, 2),
            material_breakdown={
                f"rebar_D{int(bar_diameter_mm)}_kg": round(gross_mass_kg, 2),
                "rebar_12m_bars_count": math.ceil(effective_length_m * (1.0 + w) / 12.0),
                "binding_wire_kg": round(binding_wire_kg, 2),
            },
            measurement_status=geom_status,
            input_geometry_status=geom_status,
            derived_material_status=derived_status,
            measurement_evidence_id=ev_id,
            formula_reference="m_unit = d_mm^2 / 162.2 kg/m; M_gross = L*(1+lap)*m_unit*(1+waste)",
        )

    @staticmethod
    def estimate_masonry(
        wall_area_m2: float,
        opening_area_m2: float = 0.0,
        block_length_m: float = 0.40,
        block_height_m: float = 0.20,
        block_width_m: float = 0.20,
        mortar_joint_m: float = 0.02,
        measurement_evidence: Optional[SignedMeasurementEvidence] = None,
        is_verified_measurement: Optional[bool] = None,
    ) -> TakeoffResult:
        net_area_m2 = max(0.0, wall_area_m2 - opening_area_m2)
        if net_area_m2 <= 0:
            raise ValueError("Net masonry wall area must be positive.")
        geom_status, derived_status, ev_id = _resolve_evidence_provenance(
            measurement_evidence, is_verified_measurement
        )
        stones_per_m2 = 1.0 / ((block_length_m + mortar_joint_m) * (block_height_m + mortar_joint_m))
        net_stones = net_area_m2 * stones_per_m2
        w_stone = DEFAULT_WASTE_ALLOWANCE["masonry_stone"]
        gross_stones = math.ceil(net_stones * (1.0 + w_stone))
        stone_face_ratio = (block_length_m * block_height_m) * stones_per_m2
        mortar_vol_m3 = net_area_m2 * block_width_m * (1.0 - stone_face_ratio) * (1.0 + DEFAULT_WASTE_ALLOWANCE["mortar"])
        cement_bags = mortar_vol_m3 * 1.33 * 7.6
        sand_m3 = mortar_vol_m3 * 1.33 * 1.05
        hoop_iron_kg = (net_area_m2 / (block_height_m * 2.0)) * 0.12
        return TakeoffResult(
            component_type="Masonry",
            net_quantity=round(net_area_m2, 2),
            unit="m2",
            waste_factor=w_stone,
            gross_quantity=round(net_area_m2 * (1.0 + w_stone), 2),
            material_breakdown={
                "quarry_stones_pieces": float(gross_stones),
                "cement_50kg_bags": round(cement_bags, 2),
                "sand_m3": round(sand_m3, 2),
                "hoop_iron_kg": round(hoop_iron_kg, 2),
            },
            measurement_status=geom_status,
            input_geometry_status=geom_status,
            derived_material_status=derived_status,
            measurement_evidence_id=ev_id,
            formula_reference="N_stones = A_net / ((L_b+j)*(H_b+j)) * (1+w_s)",
        )

    @staticmethod
    def estimate_roofing(
        plan_area_m2: float,
        pitch_degrees: float = 22.5,
        effective_sheet_coverage_m2: float = 2.28,
        measurement_evidence: Optional[SignedMeasurementEvidence] = None,
        is_verified_measurement: Optional[bool] = None,
    ) -> TakeoffResult:
        if plan_area_m2 <= 0 or not (0 <= pitch_degrees < 75):
            raise ValueError("Invalid roof plan area or pitch angle.")
        geom_status, derived_status, ev_id = _resolve_evidence_provenance(
            measurement_evidence, is_verified_measurement
        )
        slope_area_m2 = plan_area_m2 / math.cos(math.radians(pitch_degrees))
        w = DEFAULT_WASTE_ALLOWANCE["roofing_sheets"]
        gross_area_m2 = slope_area_m2 * (1.0 + w)
        sheets_count = math.ceil(gross_area_m2 / effective_sheet_coverage_m2)
        timber_truss_m = gross_area_m2 * 2.4
        return TakeoffResult(
            component_type="Roofing",
            net_quantity=round(slope_area_m2, 2),
            unit="m2",
            waste_factor=w,
            gross_quantity=round(gross_area_m2, 2),
            material_breakdown={
                "roofing_sheets_count": float(sheets_count),
                "structural_timber_m": round(timber_truss_m, 1),
                "roofing_nails_kg": round(sheets_count * 0.18, 2),
            },
            measurement_status=geom_status,
            input_geometry_status=geom_status,
            derived_material_status=derived_status,
            measurement_evidence_id=ev_id,
            formula_reference="A_slope = A_plan / cos(theta); N_sheets = ceil(A_slope*(1+w)/A_eff)",
        )

    @staticmethod
    def estimate_flooring(
        floor_area_m2: float,
        screed_thickness_m: float = 0.04,
        tile_size_m: float = 0.60,
        measurement_evidence: Optional[SignedMeasurementEvidence] = None,
        is_verified_measurement: Optional[bool] = None,
    ) -> TakeoffResult:
        geom_status, derived_status, ev_id = _resolve_evidence_provenance(
            measurement_evidence, is_verified_measurement
        )
        w = DEFAULT_WASTE_ALLOWANCE["floor_tiles"]
        gross_area_m2 = floor_area_m2 * (1.0 + w)
        tile_count = math.ceil(gross_area_m2 / (tile_size_m * tile_size_m))
        screed_vol_m3 = floor_area_m2 * screed_thickness_m * 1.10
        cement_bags = screed_vol_m3 * 1.33 * 8.0 + (floor_area_m2 * 0.12)
        sand_m3 = screed_vol_m3 * 1.33 * 1.05
        return TakeoffResult(
            component_type="Flooring",
            net_quantity=round(floor_area_m2, 2),
            unit="m2",
            waste_factor=w,
            gross_quantity=round(gross_area_m2, 2),
            material_breakdown={
                "floor_tiles_pieces": float(tile_count),
                "cement_50kg_bags": round(cement_bags, 2),
                "sand_m3": round(sand_m3, 2),
                "tile_grout_kg": round(floor_area_m2 * 0.35, 2),
            },
            measurement_status=geom_status,
            input_geometry_status=geom_status,
            derived_material_status=derived_status,
            measurement_evidence_id=ev_id,
            formula_reference="A_gross = A_floor*(1+w); V_screed = A_floor*t_screed*1.10",
        )

    @staticmethod
    def estimate_plastering(
        surface_area_m2: float,
        thickness_mm: float = 15.0,
        measurement_evidence: Optional[SignedMeasurementEvidence] = None,
        is_verified_measurement: Optional[bool] = None,
    ) -> TakeoffResult:
        geom_status, derived_status, ev_id = _resolve_evidence_provenance(
            measurement_evidence, is_verified_measurement
        )
        w = DEFAULT_WASTE_ALLOWANCE["plaster"]
        net_vol_m3 = surface_area_m2 * (thickness_mm / 1000.0)
        dry_vol_m3 = net_vol_m3 * 1.33 * (1.0 + w)
        cement_bags = dry_vol_m3 * 7.6
        sand_m3 = dry_vol_m3 * 1.05
        return TakeoffResult(
            component_type="Plastering",
            net_quantity=round(surface_area_m2, 2),
            unit="m2",
            waste_factor=w,
            gross_quantity=round(surface_area_m2 * (1.0 + w), 2),
            material_breakdown={
                "cement_50kg_bags": round(cement_bags, 2),
                "sand_m3": round(sand_m3, 2),
            },
            measurement_status=geom_status,
            input_geometry_status=geom_status,
            derived_material_status=derived_status,
            measurement_evidence_id=ev_id,
            formula_reference="V_dry = A * (t_mm/1000) * 1.33 * (1 + w)",
        )

    @staticmethod
    def estimate_painting(
        surface_area_m2: float,
        undercoats: int = 1,
        finishing_coats: int = 2,
        spreading_rate_m2_per_litre: float = 12.0,
        measurement_evidence: Optional[SignedMeasurementEvidence] = None,
        is_verified_measurement: Optional[bool] = None,
    ) -> TakeoffResult:
        geom_status, derived_status, ev_id = _resolve_evidence_provenance(
            measurement_evidence, is_verified_measurement
        )
        w = DEFAULT_WASTE_ALLOWANCE["paint"]
        undercoat_litres = (surface_area_m2 * undercoats / spreading_rate_m2_per_litre) * (1.0 + w)
        finish_litres = (surface_area_m2 * finishing_coats / spreading_rate_m2_per_litre) * (1.0 + w)
        return TakeoffResult(
            component_type="Painting",
            net_quantity=round(surface_area_m2, 2),
            unit="m2",
            waste_factor=w,
            gross_quantity=round(surface_area_m2 * (1.0 + w), 2),
            material_breakdown={
                "primer_undercoat_litres": round(undercoat_litres, 2),
                "emulsion_finish_litres": round(finish_litres, 2),
                "filler_putty_kg": round(surface_area_m2 * 0.25, 2),
            },
            measurement_status=geom_status,
            input_geometry_status=geom_status,
            derived_material_status=derived_status,
            measurement_evidence_id=ev_id,
            formula_reference="L_paint = (A * N_coats / R_spread) * (1 + w)",
        )

    @staticmethod
    def estimate_electrical(
        lighting_points: int,
        socket_points: int,
        avg_run_per_point_m: float = 11.5,
        measurement_evidence: Optional[SignedMeasurementEvidence] = None,
        is_verified_measurement: Optional[bool] = None,
    ) -> TakeoffResult:
        geom_status, derived_status, ev_id = _resolve_evidence_provenance(
            measurement_evidence, is_verified_measurement
        )
        total_points = lighting_points + socket_points
        w = DEFAULT_WASTE_ALLOWANCE["electrical_cable"]
        lighting_cable_m = lighting_points * avg_run_per_point_m * (1.0 + w)
        socket_cable_m = socket_points * avg_run_per_point_m * (1.0 + w)
        conduit_m = total_points * (avg_run_per_point_m * 0.75) * (1.0 + w)
        return TakeoffResult(
            component_type="Electrical Installations",
            net_quantity=float(total_points),
            unit="points",
            waste_factor=w,
            gross_quantity=round(total_points * (1.0 + w), 2),
            material_breakdown={
                "cable_1_5mm2_m": round(lighting_cable_m, 1),
                "cable_2_5mm2_m": round(socket_cable_m, 1),
                "pvc_conduit_20mm_m": round(conduit_m, 1),
                "switches_and_sockets_pcs": float(total_points),
            },
            measurement_status=geom_status,
            input_geometry_status=geom_status,
            derived_material_status=derived_status,
            measurement_evidence_id=ev_id,
            formula_reference="L_cable = N_pts * L_avg * (1 + w)",
        )

    @staticmethod
    def estimate_plumbing(
        sanitary_fixtures_count: int,
        avg_supply_pipe_per_fixture_m: float = 6.5,
        avg_waste_pipe_per_fixture_m: float = 4.5,
        measurement_evidence: Optional[SignedMeasurementEvidence] = None,
        is_verified_measurement: Optional[bool] = None,
    ) -> TakeoffResult:
        geom_status, derived_status, ev_id = _resolve_evidence_provenance(
            measurement_evidence, is_verified_measurement
        )
        w = DEFAULT_WASTE_ALLOWANCE["plumbing_pipe"]
        ppr_pipe_m = sanitary_fixtures_count * avg_supply_pipe_per_fixture_m * (1.0 + w)
        upvc_waste_m = sanitary_fixtures_count * avg_waste_pipe_per_fixture_m * (1.0 + w)
        return TakeoffResult(
            component_type="Plumbing Installations",
            net_quantity=float(sanitary_fixtures_count),
            unit="fixtures",
            waste_factor=w,
            gross_quantity=round(sanitary_fixtures_count * (1.0 + w), 2),
            material_breakdown={
                "ppr_supply_pipe_25mm_m": round(ppr_pipe_m, 1),
                "upvc_waste_pipe_110mm_m": round(upvc_waste_m, 1),
                "fittings_and_valves_pcs": float(sanitary_fixtures_count * 6),
            },
            measurement_status=geom_status,
            input_geometry_status=geom_status,
            derived_material_status=derived_status,
            measurement_evidence_id=ev_id,
            formula_reference="L_pipe = N_fix * L_avg * (1 + w)",
        )

    @staticmethod
    def estimate_finishing_works(
        ceiling_area_m2: float,
        skirting_length_m: float,
        measurement_evidence: Optional[SignedMeasurementEvidence] = None,
        is_verified_measurement: Optional[bool] = None,
    ) -> TakeoffResult:
        geom_status, derived_status, ev_id = _resolve_evidence_provenance(
            measurement_evidence, is_verified_measurement
        )
        w = 0.07
        gypsum_boards = math.ceil((ceiling_area_m2 * (1.0 + w)) / 2.88)
        studs_and_channels_m = ceiling_area_m2 * 3.1 * (1.0 + w)
        return TakeoffResult(
            component_type="Finishing Works",
            net_quantity=round(ceiling_area_m2, 2),
            unit="m2",
            waste_factor=w,
            gross_quantity=round(ceiling_area_m2 * (1.0 + w), 2),
            material_breakdown={
                "gypsum_boards_9mm_pcs": float(gypsum_boards),
                "galvanized_steel_channels_m": round(studs_and_channels_m, 1),
                "skirting_timber_or_tile_m": round(skirting_length_m * (1.0 + w), 1),
            },
            measurement_status=geom_status,
            input_geometry_status=geom_status,
            derived_material_status=derived_status,
            measurement_evidence_id=ev_id,
            formula_reference="N_boards = ceil(A_ceil * (1 + w) / 2.88)",
        )
