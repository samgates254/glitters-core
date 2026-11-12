"""
GLITTERS CORE Django/PostgreSQL Anti-Corruption Layer (ACL) & Transactional Persistence Mapper.
Addresses Section 1 & Section 5 Audit Requirements:
- Demonstrates concrete integration between GLITTERS CORE (Django/PostgreSQL records, RBAC,
  organization boundaries, EBK/BORAQS verification registry, signed QS measurement ledger)
  and the isolated `glitters_construction_engine` optimization domain.
- Ensures the optimization engine never independently approves an engineer, fabricates a quote,
  or persists unverified/simulated outputs over actual ledger records.
"""
from __future__ import annotations
from dataclasses import dataclass
import datetime
from typing import Any, Dict, List, Optional
from ..project_model import (
    Activity,
    ConstructionProject,
    DependencyType,
    EngineeringSpecification,
    KenyanWorkCalendar,
    MoneyKES,
    PrecedenceDependency,
    ProfessionalEngineeringApproval,
    ProjectArchetype,
    ResourceCategory,
    ResourcePoolItem,
)
from ..quantity_takeoff import SignedMeasurementEvidence
from ..procurement import QuotationVerificationStatus, SupplierQuotation


@dataclass(frozen=True)
class CoreOrganizationContext:
    organization_id: str
    tenant_schema: str
    authorized_user_id: str
    user_role: str  # e.g., "LICENSED_STRUCTURAL_ENGINEER", "REGISTERED_QS", "PROCUREMENT_DIRECTOR"
    has_procurement_signoff_authority: bool = False
    has_engineering_signoff_authority: bool = False


class GlittersCoreIntegrationAdapter:
    """
    Stateless Anti-Corruption Layer (ACL) translating GLITTERS CORE PostgreSQL/Django dictionaries
    or model instances into immutable `glitters_construction_engine` domain objects, and wrapping
    solver outputs into transactional persistence payloads with strict authorization gates.
    """

    @staticmethod
    def map_core_engineering_approval(
        core_record: Dict[str, Any],
        authority_secret: bytes,
    ) -> ProfessionalEngineeringApproval:
        if not core_record.get("verified_by_ebk_boraqs_registry", False):
            raise PermissionError(
                f"GLITTERS CORE ACL: Engineer record '{core_record.get('registration_number')}' "
                f"has not been verified against the EBK/BORAQS registry."
            )
        return ProfessionalEngineeringApproval.create_verified_approval(
            approval_id=str(core_record["approval_id"]),
            engineer_full_name=str(core_record["engineer_full_name"]),
            regulatory_body=str(core_record["regulatory_body"]),
            registration_number=str(core_record["registration_number"]),
            license_valid_until=datetime.date.fromisoformat(str(core_record["license_valid_until"])),
            approved_drawing_ref=str(core_record["approved_drawing_ref"]),
            drawing_content_bytes=str(core_record["drawing_sha256_seed"]).encode("utf-8"),
            secret_key=authority_secret,
        )

    @staticmethod
    def map_core_measurement_evidence(
        core_record: Dict[str, Any],
        authority_secret: bytes,
    ) -> SignedMeasurementEvidence:
        if not core_record.get("qs_signed_off", False):
            raise PermissionError(
                f"GLITTERS CORE ACL: Measurement sheet '{core_record.get('measurement_id')}' "
                f"lacks Quantity Surveyor digital signature."
            )
        return SignedMeasurementEvidence.create_verified_measurement_evidence(
            measurement_id=str(core_record["measurement_id"]),
            surveyor_full_name=str(core_record["surveyor_full_name"]),
            professional_reg_no=str(core_record["professional_reg_no"]),
            source_document_ref=str(core_record["source_document_ref"]),
            verifier_id=str(core_record["verifier_id"]),
            measurement_date=datetime.date.fromisoformat(str(core_record["measurement_date"])),
            document_bytes=str(core_record["document_content_seed"]).encode("utf-8"),
            secret_key=authority_secret,
        )

    @staticmethod
    def build_transactional_procurement_payload(
        org_context: CoreOrganizationContext,
        procurement_result: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Guards persistence of procurement purchase orders into GLITTERS CORE PostgreSQL tables.
        Blocks persistence if the run used SIMULATION mode or if the caller lacks procurement authority.
        """
        if not org_context.has_procurement_signoff_authority:
            raise PermissionError(
                f"User '{org_context.authorized_user_id}' (role={org_context.user_role}) "
                f"is not authorized to commit purchase orders in organization '{org_context.organization_id}'."
            )
        if not procurement_result.get("production_procurement_authorized", False):
            raise PermissionError(
                "Cannot persist SIMULATION procurement results as binding purchase orders in GLITTERS CORE."
            )
        total_cents = int(procurement_result["total_procurement_cost_cents"])
        return {
            "organization_id": org_context.organization_id,
            "authorized_by_user_id": org_context.authorized_user_id,
            "material_id": procurement_result["material_id"],
            "total_obligation_cents": total_cents,
            "total_obligation_kes": MoneyKES(total_cents).to_kes(),
            "purchase_orders": procurement_result["allocations"],
            "ready_for_postgres_atomic_commit": True,
        }
