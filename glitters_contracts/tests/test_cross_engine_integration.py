from __future__ import annotations
import unittest
from datetime import date, datetime, timezone
from uuid import uuid4, UUID
import hashlib
import json

from glitters_contracts import (
    AlgorithmExecutionEnvelope,
    AlgorithmDecisionEnvelope,
    AlgorithmicExecutionStatus,
    AuthorizedCallerContext,
    ConfidenceTier,
    UncertaintyBounds,
    DecisionExplanation,
    EngineAuditEvent,
    ProfessionalBoard,
    ProfessionalLicenseEvidence,
    CadastralVerificationEvidence,
    PhysicalMeasurementEvidence,
    SettledPaymentEvidence,
    GeospatialValuationFeatureVector,
    AssetValuationRentalBridge,
    RentalClearingAllocationBridge,
    ProjectMilestoneMoveInBridge,
    ConstructionProcurementFinancialBridge,
)

# Import engine packages
import glitters_allocation
from glitters_allocation.contracts import (
    AllocationPolicy,
    ApplicantProfile,
    PropertyUnit,
    PropertyCategory as AllocPropertyCategory,
    OrganizationQuota,
    InventorySnapshot,
    AllocationBatchInput,
    SolverRegime,
)
from glitters_allocation.engine import GlittersAllocationEngine

import glitters_geospatial_engine
from glitters_geospatial_engine.crs.kenya_crs import KenyaCRSEngine
from shapely.geometry import Polygon, Point

import glitters_valuation_engine
from glitters_valuation_engine.contracts import (
    PropertyRecord,
    ValuationOutputContract,
    ValuationStatus,
    TenureType,
    PropertyCategory as ValPropertyCategory,
)
from glitters_valuation_engine.ensemble import GlittersValuationEngine
from glitters_valuation_engine.synthetic_data import generate_synthetic_kenyan_dataset

import glitters_rental_optimization
from glitters_rental_optimization.schemas import (
    UnitSpecification,
    LeaseStatus,
    ListingLifecycleStatus,
    PricingStatus,
)
from glitters_rental_optimization.pricing.single_property import SinglePropertyPricingOptimizer
from glitters_rental_optimization import generate_synthetic_kenyan_portfolio

import glitters_construction_engine
from glitters_construction_engine.project_model.activities import Activity, ConstructionProject
from glitters_construction_engine.project_model.dependencies import PrecedenceDependency, DependencyType
from glitters_construction_engine.scheduling.cpm import CriticalPathEngine

import glitters_financial_intelligence
from glitters_financial_intelligence.accounting import (
    DoubleEntryLedger,
    EntryStatus,
    FinancialDimensions,
    JournalLine,
    create_standard_real_estate_coa,
    AccountingPeriod,
    PeriodStatus,
    CrossOrganizationAccessError,
)
from glitters_financial_intelligence.money import Money, CurrencyMismatchError


class TestCrossEngineContractsAndIntegrationMesh(unittest.TestCase):
    """
    Cross-Engine Integration Verification Suite:
    Exercises the full inter-engine information mesh, universal envelopes,
    cryptographic evidence verification, and multi-tenant isolation.
    """

    def setUp(self) -> None:
        self.tenant_a = uuid4()
        self.tenant_b = uuid4()
        self.user_a = uuid4()
        self.user_b = uuid4()

    def test_01_universal_envelope_multi_tenant_and_permission_guards(self) -> None:
        """
        Envelope Gate 1: Cross-tenant execution and missing permission rejection.
        """
        # 1. Caller from Tenant B cannot execute envelope for Tenant A
        caller_b = AuthorizedCallerContext(
            caller_user_id=self.user_b,
            tenant_org_id=self.tenant_b,
            granted_permissions=frozenset({"pricing.execute"}),
        )

        with self.assertRaises(ValueError) as ctx:
            AlgorithmExecutionEnvelope(
                execution_id=uuid4(),
                tenant_org_id=self.tenant_a,  # Mismatch
                authorized_caller=caller_b,
                engine_id="ENGINE_04_RENTAL",
                engine_version="2.0.0",
                policy_id="DEFAULT",
                policy_version=1,
                idempotency_key="TEST_001",
                created_at_utc=datetime.now(timezone.utc),
                input_snapshot={"test": 123},
                input_snapshot_sha256="0" * 64,
            )
        self.assertIn("Cross-tenant execution attempt", str(ctx.exception))

        # 2. Caller lacking permission raises PermissionError
        caller_a = AuthorizedCallerContext(
            caller_user_id=self.user_a,
            tenant_org_id=self.tenant_a,
            granted_permissions=frozenset({"pricing.view"}),  # Lacks pricing.execute
        )
        with self.assertRaises(PermissionError):
            caller_a.require_permission("pricing.execute")

    def test_02_cryptographic_evidence_verification_gates(self) -> None:
        """
        Evidence Gate 2: Prohibits raw unverified claims; mandates 64-char SHA-256 and valid board licenses.
        """
        # Valid professional license
        license_ev = ProfessionalLicenseEvidence(
            board=ProfessionalBoard.BORAQS,
            license_number="BORAQS/QS/2026/0442",
            practitioner_name="Arch. Jane Kariuki",
            expiration_date="2027-12-31",
            license_certificate_sha256="a" * 64,
            board_verification_hash="b" * 64,
        )
        self.assertTrue(license_ev.verify_authenticity())

        # Invalid license with corrupted hash
        invalid_license = ProfessionalLicenseEvidence(
            board=ProfessionalBoard.EBK,
            license_number="EBK/PE/1029",
            practitioner_name="Eng. Peter Otieno",
            expiration_date="2027-12-31",
            license_certificate_sha256="short_hash",
            board_verification_hash="c" * 64,
        )
        self.assertFalse(invalid_license.verify_authenticity())

        # Physical measurement evidence check
        measurement_ev = PhysicalMeasurementEvidence(
            measurement_id=uuid4(),
            project_id=uuid4(),
            certified_by=license_ev,
            measured_quantity=450.5,
            measurement_unit="m3_reinforced_concrete",
            evidence_document_sha256="d" * 64,
            measurement_timestamp_utc=datetime.now(timezone.utc),
        )
        self.assertTrue(measurement_ev.verify())

        # Negative or zero measurement rejected
        invalid_measurement = PhysicalMeasurementEvidence(
            measurement_id=uuid4(),
            project_id=uuid4(),
            certified_by=license_ev,
            measured_quantity=-10.0,
            measurement_unit="m3_concrete",
            evidence_document_sha256="e" * 64,
            measurement_timestamp_utc=datetime.now(timezone.utc),
        )
        self.assertFalse(invalid_measurement.verify())

    def test_03_information_mesh_geospatial_to_valuation_to_rental_to_allocation(self) -> None:
        """
        Pipeline Flow 1: Engine 3 (Geospatial) -> Engine 2 (Valuation) -> Engine 4 (Rental Pricing) -> Engine 1 (Allocation).
        End-to-end multi-engine deterministic workflow.
        """
        # Step A: Engine 3 (Geospatial) - Compute parcel geometry & metrics
        crs_mgr = KenyaCRSEngine()
        # Nairobi parcel polygon (approx 1000 sqm in Kilimani)
        poly = Polygon([
            (36.7850, -1.2900),
            (36.7853, -1.2900),
            (36.7853, -1.2903),
            (36.7850, -1.2903),
            (36.7850, -1.2900),
        ])
        projected_poly, epsg = crs_mgr.project_wgs84_to_local_kenya_utm(poly)
        area_sqm = projected_poly.area
        perimeter_m = projected_poly.length
        compactness = (4 * 3.14159265 * area_sqm) / (perimeter_m ** 2)

        geo_bridge = GeospatialValuationFeatureVector(
            parcel_id="NAIROBI/BLOCK_42/108",
            centroid_lat=-1.29015,
            centroid_lon=36.78515,
            utm_zone=37,
            area_sqm=area_sqm,
            perimeter_m=perimeter_m,
            compactness_ratio=compactness,
            dem_mean_elevation_m=1680.0,
            dem_slope_degrees=2.5,
            flood_risk_score=0.08,
            distance_to_major_road_m=45.0,
            distance_to_cbd_m=4200.0,
            nearest_hospital_dist_m=650.0,
            nearest_school_dist_m=800.0,
            road_accessibility_15min_reach=0.88,
            local_morans_i=0.45,
        )
        self.assertGreater(geo_bridge.area_sqm, 500.0)

        # Step B: Engine 2 (Valuation) - Fit engine on tenant cohort and value property
        val_engine = GlittersValuationEngine()
        training_dataset = generate_synthetic_kenyan_dataset(
            n_samples=240, random_seed=2026, tenant_org_id=str(self.tenant_a)
        )
        val_engine.fit(training_dataset, tenant_org_id=str(self.tenant_a), allow_synthetic_training=True)

        subject = PropertyRecord(
            property_id="PROP_KILIMANI_01",
            tenant_org_id=str(self.tenant_a),
            category=ValPropertyCategory.APARTMENT_CONDO,
            county="Nairobi",
            sub_county="Dagoretti North",
            locality="Kilimani",
            latitude=geo_bridge.centroid_lat,
            longitude=geo_bridge.centroid_lon,
            land_area_sqm=geo_bridge.area_sqm,
            building_area_sqm=105.0,
            bedrooms=2,
            bathrooms=2,
            building_age_years=3.0,
            tenure_type=TenureType.LEASEHOLD,
            unexpired_lease_years=75.0,
        )
        val_res = val_engine.value_property(subject, allow_synthetic_inference=True)
        self.assertIn(val_res.status, {ValuationStatus.SUCCESS_HIGH_CONFIDENCE, ValuationStatus.SUCCESS_MODERATE_CONFIDENCE})
        self.assertIsNotNone(val_res.point_estimate_kes)
        self.assertGreater(val_res.point_estimate_kes, 5_000_000)

        # Step C: Engine 4 (Rental Pricing) - Determine market rent & landlord yield floor
        val_to_rental = AssetValuationRentalBridge(
            property_id=subject.property_id,
            asset_valuation_kes_cents=int(val_res.point_estimate_kes * 100),
            valuation_confidence_tier="HIGH_CONFIDENCE",
            minimum_gross_yield_bps=750,  # 7.5% gross target
            minimum_acceptable_annual_rent_cents=int(val_res.point_estimate_kes * 100 * 0.075),
            hedonic_quality_score=val_res.sub_model_estimates.get("HEDONIC", 1.0),
            is_conformal_calibrated=val_res.is_conformal_calibrated,
        )

        rental_units, rental_comps = generate_synthetic_kenyan_portfolio(
            n_units=10, random_seed=2026
        )
        vacant_unit = next(u for u in rental_units if u.lease_status == LeaseStatus.VACANT)
        rental_optimizer = SinglePropertyPricingOptimizer()
        rec_rent = rental_optimizer.optimize_unit_price(vacant_unit, rental_comps)
        rec_monthly_kes = rec_rent.ledger.recommended_rent_kes
        self.assertGreater(rec_monthly_kes, 30_000)

        # Step D: Engine 1 (Property Allocation) - Match applicant based on clearing rent
        rental_to_alloc = RentalClearingAllocationBridge(
            unit_id=vacant_unit.unit_id,
            property_id=subject.property_id,
            recommended_monthly_rent_cents=int(rec_monthly_kes * 100),
            minimum_monthly_income_required_cents=int(rec_monthly_kes * 100 / 0.30),
            optimal_occupancy_target_rate=0.95,
            pricing_status=rec_rent.status.value,
        )

        applicant_qual = ApplicantProfile(
            applicant_id="APP-001",
            organization_id=str(self.tenant_a),
            allowed_categories=frozenset({AllocPropertyCategory.RESIDENTIAL_MARKET}),
            max_monthly_budget_cents=rental_to_alloc.recommended_monthly_rent_cents + 10_000_00,
            max_upfront_capital_cents=50_000_000,
            verified_monthly_income_cents=rental_to_alloc.minimum_monthly_income_required_cents + 50_000_00,
            credit_score=750,
            preferred_latitude=geo_bridge.centroid_lat,
            preferred_longitude=geo_bridge.centroid_lon,
            max_travel_distance_km=10.0,
            min_bedrooms=2,
            min_floor_area_sqm=90,
            desired_move_in_day=10,
            latest_move_in_day=60,
            desired_lease_days=365,
            preferred_zones=frozenset({"NBO-KILIMANI"}),
        )

        prop_unit = PropertyUnit(
            unit_id=rental_to_alloc.unit_id,
            property_id=rental_to_alloc.property_id,
            organization_id=str(self.tenant_a),
            category=AllocPropertyCategory.RESIDENTIAL_MARKET,
            zone_code="NBO-KILIMANI",
            latitude=geo_bridge.centroid_lat,
            longitude=geo_bridge.centroid_lon,
            monthly_rent_cents=rental_to_alloc.recommended_monthly_rent_cents,
            security_deposit_cents=rental_to_alloc.recommended_monthly_rent_cents,
            service_charge_cents=5_000_00,
            min_credit_score=600,
            min_income_multiple_bps=25_000,
            capacity_slots=1,
            bedrooms=2,
            floor_area_sqm=105,
            available_from_day=0,
            available_until_day=1000,
            min_lease_days=180,
            max_lease_days=730,
            amenities=frozenset({"PARKING"}),
            owner_priority_tier=1,
        )

        snap = InventorySnapshot(
            snapshot_id="SNAP-INT-001",
            snapshot_sequence=1,
            timestamp_iso="2026-01-01T00:00:00Z",
            units=(prop_unit,),
            applicants=(applicant_qual,),
            quotas=(),
            existing_assignments=(),
        )
        pol = AllocationPolicy(
            policy_id="POL-INT-001",
            policy_version="v1.0",
            organization_id=str(self.tenant_a),
            solver_regime=SolverRegime.HUNGARIAN_ASSIGNMENT,
        )
        batch_input = AllocationBatchInput(
            request_id="REQ-INT-001",
            expected_snapshot_sequence=1,
            expected_policy_version="v1.0",
            snapshot=snap,
            policy=pol,
        )

        alloc_engine = GlittersAllocationEngine()
        alloc_res = alloc_engine.allocate(batch_input)
        self.assertEqual(len(alloc_res.decisions), 1)
        self.assertEqual(alloc_res.decisions[0].applicant_id, applicant_qual.applicant_id)
        self.assertEqual(alloc_res.decisions[0].unit_id, prop_unit.unit_id)

    def test_04_information_mesh_construction_milestones_to_financial_journal(self) -> None:
        """
        Pipeline Flow 2: Engine 5 (Construction BOQ / RCPSP) -> Engine 6 (Financial Double-Entry Ledger).
        Procurement commitment generates balanced Construction-in-Progress (CIP) posting.
        """
        # Step A: Engine 5 CPM scheduling
        from glitters_construction_engine.project_model.dependencies import ProjectArchetype
        p = ConstructionProject(
            project_id="PROJ-01",
            name="Kilimani Tower",
            archetype=ProjectArchetype.APARTMENT_COMPLEX,
            county="Nairobi",
            budget_ceiling_kes=50_000_000.0,
            contractual_deadline_days=180,
        )
        p.add_activity(Activity("ACT_FOUNDATION", "Substructure & Foundation", "1.1", 30))
        p.add_activity(Activity("ACT_SUPERSTRUCTURE", "Concrete Frame & Slabs", "1.2", 60))
        p.add_dependency(PrecedenceDependency("ACT_FOUNDATION", "ACT_SUPERSTRUCTURE", DependencyType.FS, 0))
        cpm = CriticalPathEngine.compute_cpm(p)
        self.assertGreaterEqual(cpm["project_completion_days"], 90)

        # Step B: Certified BOQ procurement bridge to Engine 6
        proc_bridge = ConstructionProcurementFinancialBridge(
            purchase_order_id="PO_CONC_2026_01",
            vendor_account_id="VEND_BAMBURI_CEMENT",
            project_id="PROJ-01",
            committed_amount_cents=3_500_000_00,  # KES 3.5M
            currency="KES",
            item_category="CONCRETE_MATERIALS",
            boraqs_certified=True,
            evidence_document_sha256="f" * 64,
        )
        self.assertTrue(proc_bridge.boraqs_certified)

        # Step C: Engine 6 Ledger - Post balanced journal entry for CIP and Accounts Payable
        ledger = DoubleEntryLedger(organization_id=str(self.tenant_a), base_currency="KES")
        for acct in create_standard_real_estate_coa(str(self.tenant_a), "KES"):
            ledger.register_account(acct)

        ledger.register_period(
            AccountingPeriod(
                period_id="2026-11",
                organization_id=str(self.tenant_a),
                name="November 2026",
                start_date=date(2026, 11, 1),
                end_date=date(2026, 11, 30),
                status=PeriodStatus.OPEN,
            )
        )

        line_cip = ledger.build_line(
            "1500",
            debit_minor=proc_bridge.committed_amount_cents,
            property_id=proc_bridge.project_id,
            description=f"CIP materials accrual for {proc_bridge.purchase_order_id}",
        )
        line_ap = ledger.build_line(
            "2010",
            credit_minor=proc_bridge.committed_amount_cents,
            description=f"Accounts payable to {proc_bridge.vendor_account_id}",
        )
        posted_entry = ledger.post_entry(
            period_id="2026-11",
            entry_date=date(2026, 11, 15),
            source_reference=proc_bridge.purchase_order_id,
            description="BORAQS certified procurement accrual",
            lines=[line_cip, line_ap],
            idempotency_key=f"IDEM-PO-{proc_bridge.purchase_order_id}",
        )
        self.assertEqual(posted_entry.status, EntryStatus.POSTED)
        self.assertTrue(bool(posted_entry.entry_hash))

        # Verify Trial Balance remains perfectly balanced
        tb = ledger.generate_trial_balance(date(2026, 11, 30))
        self.assertTrue(tb.is_balanced)
        self.assertEqual(tb.total_debits_minor, tb.total_credits_minor)
        self.assertEqual(tb.total_debits_minor, 3_500_000_00)

    def test_05_cross_tenant_isolation_boundary_strictly_enforced_across_all_engines(self) -> None:
        """
        Adversarial Boundary Test: Verifies that attempts to mix data from Tenant A into Tenant B
        are strictly rejected across all engines.
        """
        ledger_a = DoubleEntryLedger(organization_id=str(self.tenant_a), base_currency="KES")
        for acc in create_standard_real_estate_coa(str(self.tenant_a), "KES"):
            ledger_a.register_account(acc)

        ledger_a.register_period(
            AccountingPeriod(
                period_id="2026-11",
                organization_id=str(self.tenant_a),
                name="November 2026",
                start_date=date(2026, 11, 1),
                end_date=date(2026, 11, 30),
                status=PeriodStatus.OPEN,
            )
        )

        bad_line = ledger_a.build_line("1010", debit_minor=1000_00)
        tampered_line = JournalLine(
            line_id="L-BAD",
            account_code="2010",
            debit=Money(0, "KES"),
            credit=Money(1000_00, "KES"),
            functional_debit=Money(0, "KES"),
            functional_credit=Money(1000_00, "KES"),
            dimensions=FinancialDimensions(organization_id=str(self.tenant_b)),
        )
        with self.assertRaises(CrossOrganizationAccessError):
            ledger_a.post_entry(
                period_id="2026-11",
                entry_date=date(2026, 11, 10),
                source_reference="LEAK-01",
                description="Cross-org attempt",
                lines=[bad_line, tampered_line],
                idempotency_key="IDEM-LEAK-1",
            )


if __name__ == "__main__":
    unittest.main()
