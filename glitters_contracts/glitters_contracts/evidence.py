from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional
from uuid import UUID
import hashlib


class ProfessionalBoard(str, Enum):
    EBK = "ENGINEERS_BOARD_OF_KENYA"
    BORAQS = "BOARD_OF_REGISTRATION_OF_ARCHITECTS_AND_QUANTITY_SURVEYORS"
    ISK = "INSTITUTION_OF_SURVEYORS_OF_KENYA"
    SOK = "SURVEY_OF_KENYA"
    VRB = "VALUERS_REGISTRATION_BOARD"


@dataclass(frozen=True, slots=True)
class ProfessionalLicenseEvidence:
    """Cryptographic evidence proving caller or signatory professional registration."""
    board: ProfessionalBoard
    license_number: str
    practitioner_name: str
    expiration_date: str
    license_certificate_sha256: str
    board_verification_hash: str

    def verify_authenticity(self) -> bool:
        if not self.license_certificate_sha256 or len(self.license_certificate_sha256) != 64:
            return False
        if not self.board_verification_hash or len(self.board_verification_hash) != 64:
            return False
        return True


@dataclass(frozen=True, slots=True)
class CadastralVerificationEvidence:
    """Evidence record proving legal parcel boundary registration."""
    parcel_identifier: str
    registry_index_map: str
    licensed_surveyor_license: str
    survey_plan_number: str
    survey_plan_sha256: str
    approval_timestamp_utc: datetime

    def verify_evidence(self) -> bool:
        if not self.survey_plan_sha256 or len(self.survey_plan_sha256) != 64:
            return False
        return bool(self.parcel_identifier and self.survey_plan_number)


@dataclass(frozen=True, slots=True)
class PhysicalMeasurementEvidence:
    """Evidence record for BORAQS/ISK certified quantity takeoff or site measurement."""
    measurement_id: UUID
    project_id: UUID
    certified_by: ProfessionalLicenseEvidence
    measured_quantity: float
    measurement_unit: str
    evidence_document_sha256: str
    measurement_timestamp_utc: datetime

    def verify(self) -> bool:
        if not self.certified_by.verify_authenticity():
            return False
        if not self.evidence_document_sha256 or len(self.evidence_document_sha256) != 64:
            return False
        if self.measured_quantity <= 0:
            return False
        return True


@dataclass(frozen=True, slots=True)
class SettledPaymentEvidence:
    """Proof of transaction settlement issued by financial engine or GLITTERS CORE."""
    receipt_reference: str
    external_provider: str
    amount_minor_units: int
    currency: str
    settled_at_utc: datetime
    posting_hash_sha256: str

    def verify(self) -> bool:
        return (
            bool(self.receipt_reference)
            and self.amount_minor_units > 0
            and len(self.posting_hash_sha256) == 64
        )


@dataclass(frozen=True, slots=True)
class TenantCreditEvidence:
    """Cryptographic evidence from licensed credit reference bureau (CRB)."""
    applicant_id: str
    crb_listing_status: str
    crb_score: int
    report_reference: str
    verified_at_utc: datetime
    crb_provider_hash_sha256: str

    def verify(self) -> bool:
        if not self.crb_provider_hash_sha256 or len(self.crb_provider_hash_sha256) != 64:
            return False
        return bool(self.applicant_id and self.report_reference and 0 <= self.crb_score <= 1000)


@dataclass(frozen=True, slots=True)
class TitleDeedEvidence:
    """Official Ministry of Lands / Cadastral registry search evidence."""
    title_number: str
    registry_name: str
    registered_owner_hash: str
    encumbrances_search_reference: str
    verified_at_utc: datetime
    search_certificate_sha256: str

    def verify(self) -> bool:
        if not self.search_certificate_sha256 or len(self.search_certificate_sha256) != 64:
            return False
        if not self.registered_owner_hash or len(self.registered_owner_hash) != 64:
            return False
        return bool(self.title_number and self.encumbrances_search_reference)
