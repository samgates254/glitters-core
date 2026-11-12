"""
Kenyan Construction Benchmark & Production Test Datasets.
Remediates CRITICAL 1 & CRITICAL 3 in dataset builders:
- Uses cryptographically signed `ProfessionalEngineeringApproval` with valid EBK registration
  (`EBK-A2481`) for structural activities so `validate_compliance()` tests real cryptographic authority.
- Provides both `get_synthetic_kenyan_supplier_quotations()` (explicitly marked `SYNTHETIC_BENCHMARK_QUOTATION`,
  which are blocked in production procurement unless `allow_synthetic_or_unverified_quotes=True`)
  AND `get_verified_kenyan_supplier_quotations()` (backed by SHA-256 document hashes and
  `VERIFIED_BINDING_QUOTATION` status for production procurement testing).
"""
from __future__ import annotations
import datetime
from typing import List
from ..project_model import (
    Activity,
    ConstructionProject,
    DependencyType,
    EngineeringSpecification,
    ExecutionMode,
    KenyanWorkCalendar,
    MeasurementStatus,
    PrecedenceDependency,
    ProfessionalEngineeringApproval,
    ProjectArchetype,
    ResourceCategory,
    ResourcePoolItem,
)
from ..procurement import QuotationVerificationStatus, SupplierQuotation


def build_kenyan_benchmark_project(
    project_id: str = "KE_NRB_MIXED_USE_01",
    archetype: ProjectArchetype = ProjectArchetype.MIXED_USE,
    county: str = "Nairobi",
) -> ConstructionProject:
    cal = KenyanWorkCalendar(calendar_id=f"CAL_{county.upper()}", county=county, working_days_per_week=6)
    proj = ConstructionProject(
        project_id=project_id,
        name=f"SYNTHETIC_BENCHMARK: {county} {archetype.value} Development",
        archetype=archetype,
        county=county,
        budget_ceiling_kes=85_000_000.0,
        contractual_deadline_days=145,
        project_start_date=datetime.date(2026, 11, 16),
        daily_overhead_kes=45_000.0,
        liquidated_damages_per_day_kes=85_000.0,
        calendar=cal,
        nonrenewable_material_caps={
            "cement_50kg_bags": 10000.0,
            "rebar_D16_kg": 90000.0,
            "quarry_stones_pieces": 40000.0,
            "roofing_sheets_count": 1000.0,
        },
        scheduled_material_deliveries={
            "cement_50kg_bags": [(0, 4000.0), (20, 4000.0), (50, 2000.0)],
            "rebar_D16_kg": [(0, 45000.0), (25, 45000.0)],
            "quarry_stones_pieces": [(0, 40000.0)],
            "roofing_sheets_count": [(0, 1000.0)],
        },
    )

    resources = [
        ResourcePoolItem("CIVIL_ENG", "Registered Civil Engineer", ResourceCategory.HUMAN, "Engineer", "person", 2.0, 12000.0),
        ResourcePoolItem("STRUCT_ENG", "Structural Engineer", ResourceCategory.HUMAN, "Engineer", "person", 2.0, 14000.0),
        ResourcePoolItem("SITE_SUP", "Site Supervisor", ResourceCategory.HUMAN, "Supervisor", "person", 3.0, 4500.0),
        ResourcePoolItem("SKILLED_ARTISAN", "Skilled Mason / Steel Fixer / Carpenter", ResourceCategory.HUMAN, "Skilled", "person", 24.0, 1800.0),
        ResourcePoolItem("GEN_LABOUR", "General Construction Labourer", ResourceCategory.HUMAN, "General", "person", 40.0, 950.0),
        ResourcePoolItem("EXCAVATOR", "20T Hydraulic Excavator", ResourceCategory.EQUIPMENT, "Earthmoving", "machine", 2.0, 42000.0),
        ResourcePoolItem("TOWER_CRANE", "Tower Crane 40m Jib", ResourceCategory.EQUIPMENT, "Lifting", "machine", 1.0, 55000.0),
        ResourcePoolItem("CONCRETE_MIXER", "Self-Loading Concrete Mixer 3.5m3", ResourceCategory.EQUIPMENT, "Concreting", "machine", 2.0, 22000.0),
    ]
    for r in resources:
        proj.resources[r.resource_id] = r

    verified_ebk_approval = ProfessionalEngineeringApproval.create_verified_approval(
        approval_id="APP-EBK-2026-001",
        engineer_full_name="Eng. Dr. Kamau Njoroge",
        regulatory_body="EBK",
        registration_number="EBK-A2481",
        license_valid_until=datetime.date(2027, 12, 31),
        approved_drawing_ref="DRW-STR-2026-001-REV-A",
    )

    spec_rc = EngineeringSpecification(
        spec_id="SPEC_RC_C25_B500B",
        structural_code="KS EAS 18-1:2017 / BS EN 1992-1-1",
        min_concrete_class_mpa=25.0,
        min_steel_yield_mpa=500.0,
        professional_approval=verified_ebk_approval,
    )

    activities_data = [
        ("A01", "Site Mobilization & Setting Out", "1.1", 6, 5, 6, 8, 4, 850_000.0, 1_100_000.0, {"CIVIL_ENG": 1, "SITE_SUP": 1, "GEN_LABOUR": 8}, {}),
        ("A02", "Bulk Earthworks & Foundation Excavation", "1.2", 14, 11, 14, 20, 10, 3_200_000.0, 3_950_000.0, {"EXCAVATOR": 2, "SITE_SUP": 1, "GEN_LABOUR": 12}, {}),
        ("A03", "Substructure Pad Foundations & Plinth RC", "1.3", 18, 15, 18, 25, 13, 9_400_000.0, 10_900_000.0, {"STRUCT_ENG": 1, "CONCRETE_MIXER": 2, "SKILLED_ARTISAN": 12, "GEN_LABOUR": 20}, {"cement_50kg_bags": 1400, "rebar_D16_kg": 18000}),
        ("A04", "Ground Floor Slab & Columns RC", "2.1", 20, 17, 20, 27, 15, 11_500_000.0, 13_200_000.0, {"STRUCT_ENG": 1, "TOWER_CRANE": 1, "CONCRETE_MIXER": 2, "SKILLED_ARTISAN": 14, "GEN_LABOUR": 22}, {"cement_50kg_bags": 1850, "rebar_D16_kg": 24000}),
        ("A05", "Upper Floors Superstructure RC Frame", "2.2", 26, 22, 26, 36, 19, 15_800_000.0, 18_400_000.0, {"STRUCT_ENG": 1, "TOWER_CRANE": 1, "CONCRETE_MIXER": 2, "SKILLED_ARTISAN": 16, "GEN_LABOUR": 26}, {"cement_50kg_bags": 2400, "rebar_D16_kg": 31000}),
        ("A06", "Quarry Stone Masonry Walling", "3.1", 22, 18, 22, 29, 16, 6_400_000.0, 7_450_000.0, {"SITE_SUP": 1, "SKILLED_ARTISAN": 14, "GEN_LABOUR": 18}, {"quarry_stones_pieces": 28000, "cement_50kg_bags": 620}),
        ("A07", "Structural Timber & IT5 Profiled Roofing", "3.2", 14, 12, 14, 19, 10, 4_800_000.0, 5_550_000.0, {"SITE_SUP": 1, "SKILLED_ARTISAN": 10, "GEN_LABOUR": 12}, {"roofing_sheets_count": 480}),
        ("A08", "First-Fix Electrical & Conduiting", "4.1", 12, 10, 12, 16, 9, 3_100_000.0, 3_600_000.0, {"SKILLED_ARTISAN": 8, "GEN_LABOUR": 6}, {}),
        ("A09", "First-Fix Plumbing & Drainage", "4.2", 12, 10, 12, 17, 9, 2_950_000.0, 3_450_000.0, {"SKILLED_ARTISAN": 8, "GEN_LABOUR": 8}, {}),
        ("A10", "Internal & External Cement-Sand Plastering", "5.1", 16, 13, 16, 22, 12, 3_600_000.0, 4_250_000.0, {"SKILLED_ARTISAN": 12, "GEN_LABOUR": 16}, {"cement_50kg_bags": 540}),
        ("A11", "Porcelain Floor Tiling & Acoustic Ceilings", "5.2", 14, 12, 14, 19, 10, 4_900_000.0, 5_650_000.0, {"SKILLED_ARTISAN": 10, "GEN_LABOUR": 10}, {}),
        ("A12", "Painting, Second-Fix MEP & Commissioning", "5.3", 10, 8, 10, 14, 8, 2_800_000.0, 3_300_000.0, {"CIVIL_ENG": 1, "SITE_SUP": 1, "SKILLED_ARTISAN": 10, "GEN_LABOUR": 8}, {}),
    ]

    for aid, name, wbs, dur, opt_d, ml_d, pess_d, crash_d, norm_c, crash_c, r_dem, m_dem in activities_data:
        spec = spec_rc if aid in ("A03", "A04", "A05") else None
        c_mpa = 25.0 if spec is not None else None
        s_mpa = 500.0 if spec is not None else None
        act = Activity(
            activity_id=aid,
            name=name,
            wbs_code=wbs,
            duration_days=dur,
            optimistic_days=float(opt_d),
            most_likely_days=float(ml_d),
            pessimistic_days=float(pess_d),
            crash_duration_days=crash_d,
            normal_cost_kes=norm_c,
            crash_cost_kes=crash_c,
            resource_demands=r_dem,
            material_demands=m_dem,
            engineering_spec=spec,
            concrete_mpa_designed=c_mpa,
            steel_mpa_designed=s_mpa,
        )
        if spec is not None:
            act.modes = [
                ExecutionMode(
                    mode_id="NORMAL",
                    duration_days=dur,
                    direct_cost_kes=norm_c,
                    renewable_demands=dict(r_dem),
                    nonrenewable_demands=dict(m_dem),
                    concrete_mpa_offered=25.0,
                    steel_mpa_offered=500.0,
                ),
                ExecutionMode(
                    mode_id="CRASH",
                    duration_days=crash_d,
                    direct_cost_kes=crash_c,
                    renewable_demands={k: min(proj.resources[k].max_capacity_per_day, v * 1.2) for k, v in r_dem.items()},
                    nonrenewable_demands=dict(m_dem),
                    concrete_mpa_offered=25.0,
                    steel_mpa_offered=500.0,
                ),
            ]
        proj.add_activity(act)

    deps = [
        ("A01", "A02", DependencyType.FS, 0),
        ("A02", "A03", DependencyType.FS, 0),
        ("A03", "A04", DependencyType.FS, 0),
        ("A04", "A05", DependencyType.FS, 0),
        ("A04", "A06", DependencyType.SS, 8),
        ("A05", "A07", DependencyType.FS, 0),
        ("A06", "A08", DependencyType.SS, 6),
        ("A06", "A09", DependencyType.SS, 6),
        ("A06", "A10", DependencyType.FS, 0),
        ("A08", "A10", DependencyType.FF, 2),
        ("A09", "A10", DependencyType.FF, 2),
        ("A07", "A11", DependencyType.FS, 0),
        ("A10", "A11", DependencyType.FS, 0),
        ("A11", "A12", DependencyType.FS, 0),
    ]
    for u, v, dtype, lag in deps:
        proj.add_dependency(PrecedenceDependency(u, v, dtype, lag))

    return proj


def generate_large_scale_synthetic_project(
    num_activities: int = 1200,
    county: str = "Nairobi",
) -> ConstructionProject:
    proj = ConstructionProject(
        project_id=f"KE_MEGA_{num_activities}_ACT",
        name=f"SYNTHETIC_BENCHMARK: Large-Scale {num_activities}-Activity Development",
        archetype=ProjectArchetype.LARGE_SCALE_PROPERTY,
        county=county,
        budget_ceiling_kes=5_000_000_000.0,
        contractual_deadline_days=num_activities * 3,
    )
    proj.resources["SKILLED_ARTISAN"] = ResourcePoolItem(
        "SKILLED_ARTISAN", "Skilled Crew", ResourceCategory.HUMAN, "Skilled", "person", 120.0, 1800.0
    )
    proj.resources["GEN_LABOUR"] = ResourcePoolItem(
        "GEN_LABOUR", "General Crew", ResourceCategory.HUMAN, "General", "person", 200.0, 950.0
    )

    width = 10
    for i in range(num_activities):
        aid = f"ACT_{i:04d}"
        dur = 3 + (i % 7)
        proj.add_activity(
            Activity(
                activity_id=aid,
                name=f"Work Package {i}",
                wbs_code=f"1.{(i // width) + 1}.{(i % width) + 1}",
                duration_days=dur,
                normal_cost_kes=50_000.0 * dur,
                resource_demands={"SKILLED_ARTISAN": float(4 + (i % 5)), "GEN_LABOUR": float(6 + (i % 7))},
            )
        )
        if i >= width:
            proj.add_dependency(PrecedenceDependency(f"ACT_{i - width:04d}", aid, DependencyType.FS, 0))
        if i % width != 0 and i >= 1:
            proj.add_dependency(PrecedenceDependency(f"ACT_{i - 1:04d}", aid, DependencyType.SS, 1))

    return proj


def get_synthetic_kenyan_supplier_quotations() -> List[SupplierQuotation]:
    """
    Returns quotations explicitly tagged `SYNTHETIC_BENCHMARK_QUOTATION`.
    By design (CRITICAL 3 remediation), `ProcurementEngine.optimize_supplier_selection_milp()`
    REJECTS these quotations in production mode (`allow_synthetic_or_unverified_quotes=False`).
    """
    return [
        SupplierQuotation(
            supplier_id="SUP_ATHI_CEMENT_01",
            supplier_name="SYNTHETIC_BENCHMARK: Athi River Bulk Cement Depot",
            material_id="cement_50kg_bags",
            county="Machakos",
            unit_price_kes=790.0,
            min_order_quantity=200.0,
            max_supply_capacity=5000.0,
            lead_time_days=3,
            reliability_score=0.96,
            fixed_delivery_fee_kes=28000.0,
            quotation_ref="SYNTHETIC-QUOTE-001",
            verification_status=QuotationVerificationStatus.SYNTHETIC_BENCHMARK_QUOTATION,
        ),
        SupplierQuotation(
            supplier_id="SUP_MSA_CEMENT_02",
            supplier_name="SYNTHETIC_BENCHMARK: Coast Clinker & Cement Works",
            material_id="cement_50kg_bags",
            county="Mombasa",
            unit_price_kes=760.0,
            min_order_quantity=500.0,
            max_supply_capacity=8000.0,
            lead_time_days=6,
            reliability_score=0.92,
            fixed_delivery_fee_kes=65000.0,
            quotation_ref="SYNTHETIC-QUOTE-002",
            verification_status=QuotationVerificationStatus.SYNTHETIC_BENCHMARK_QUOTATION,
        ),
    ]


def get_verified_kenyan_supplier_quotations() -> List[SupplierQuotation]:
    """
    Returns cryptographically hashed, non-expired `VERIFIED_BINDING_QUOTATION` records
    suitable for testing authorized production procurement.
    """
    return [
        SupplierQuotation.create_verified_quotation(
            supplier_id="SUP_ATHI_VERIFIED_01",
            supplier_name="Athi River Cement Industrial Distributors Ltd",
            material_id="cement_50kg_bags",
            county="Machakos",
            unit_price_kes=790.0,
            min_order_quantity=200.0,
            max_supply_capacity=5000.0,
            lead_time_days=3,
            reliability_score=0.96,
            fixed_delivery_fee_kes=28000.0,
            quotation_ref="QT-KE-2026-8841",
            document_bytes=b"VERIFIED-PROFORMA-ATHI-2026-8841",
        ),
        SupplierQuotation.create_verified_quotation(
            supplier_id="SUP_MSA_VERIFIED_02",
            supplier_name="Mombasa Clinker & Cement Supply Co.",
            material_id="cement_50kg_bags",
            county="Mombasa",
            unit_price_kes=760.0,
            min_order_quantity=500.0,
            max_supply_capacity=8000.0,
            lead_time_days=6,
            reliability_score=0.92,
            fixed_delivery_fee_kes=65000.0,
            quotation_ref="QT-KE-2026-8842",
            document_bytes=b"VERIFIED-PROFORMA-MSA-2026-8842",
        ),
        SupplierQuotation.create_verified_quotation(
            supplier_id="SUP_NRB_VERIFIED_03",
            supplier_name="Industrial Area Rapid Builders Depot Ltd",
            material_id="cement_50kg_bags",
            county="Nairobi",
            unit_price_kes=835.0,
            min_order_quantity=50.0,
            max_supply_capacity=2000.0,
            lead_time_days=1,
            reliability_score=0.98,
            fixed_delivery_fee_kes=12000.0,
            quotation_ref="QT-KE-2026-8843",
            document_bytes=b"VERIFIED-PROFORMA-NRB-2026-8843",
        ),
    ]
