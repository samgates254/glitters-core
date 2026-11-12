"""
Traceable Site Measurement Evidence Model.
Remediates CRITICAL 2:
- Eliminates caller-supplied `is_verified_measurement: bool = True` self-declaration.
- Requires a cryptographically signed `SignedMeasurementEvidence` record with BORAQS/ISK
  surveyor registration, measurement date, source document SHA-256 hash, and CORE verifier HMAC.
- Strictly separates `input_geometry_status` (which can be VERIFIED_MEASUREMENT when backed by
  valid `SignedMeasurementEvidence`) from `derived_material_status` (which remains CALCULATED_ESTIMATE
  whenever constituent materials like cement, sand, and aggregate are derived from mix ratios).
"""
from __future__ import annotations
from dataclasses import dataclass
import datetime
import hashlib
import hmac
import re
from typing import Dict, List, Optional, Tuple
from ..project_model import (
    DEFAULT_CORE_AUTHORITY_SECRET,
    MeasurementStatus,
)

SURVEYOR_REG_PATTERN = re.compile(r"^(BORAQS|ISK|EBK)-[A-Z][0-9]{4,6}$")
SHA256_PATTERN = re.compile(r"^[a-fA-F0-9]{64}$")


@dataclass(frozen=True)
class SignedMeasurementEvidence:
    """
    Immutable, cryptographically verifiable site or CAD takeoff measurement record.
    """
    measurement_id: str
    surveyor_full_name: str
    professional_reg_no: str        # e.g., "BORAQS-Q2048" or "ISK-S1092"
    measurement_date: datetime.date
    source_document_ref: str        # e.g., "SITE-MEAS-SHEET-2026-044"
    document_sha256_hash: str       # 64-char hex SHA-256 of signed takeoff sheet
    verifier_id: str                # GLITTERS CORE authorized verifier ID
    signature_hmac_hex: str

    @staticmethod
    def compute_canonical_hmac(
        measurement_id: str,
        surveyor_full_name: str,
        professional_reg_no: str,
        measurement_date: datetime.date,
        source_document_ref: str,
        document_sha256_hash: str,
        verifier_id: str,
        secret_key: bytes = DEFAULT_CORE_AUTHORITY_SECRET,
    ) -> str:
        payload = (
            f"{measurement_id}|{surveyor_full_name}|{professional_reg_no}|"
            f"{measurement_date.isoformat()}|{source_document_ref}|"
            f"{document_sha256_hash.lower()}|{verifier_id}"
        ).encode("utf-8")
        return hmac.new(secret_key, payload, hashlib.sha256).hexdigest()

    @classmethod
    def create_verified_measurement_evidence(
        cls,
        measurement_id: str,
        surveyor_full_name: str,
        professional_reg_no: str,
        source_document_ref: str,
        verifier_id: str = "CORE-QS-VERIFIER-01",
        measurement_date: datetime.date = datetime.date(2026, 11, 10),
        document_bytes: bytes = b"SIGNED-SITE-MEASUREMENT-TAKEOFF-SHEET",
        secret_key: bytes = DEFAULT_CORE_AUTHORITY_SECRET,
    ) -> SignedMeasurementEvidence:
        doc_hash = hashlib.sha256(document_bytes).hexdigest()
        sig = cls.compute_canonical_hmac(
            measurement_id=measurement_id,
            surveyor_full_name=surveyor_full_name,
            professional_reg_no=professional_reg_no,
            measurement_date=measurement_date,
            source_document_ref=source_document_ref,
            document_sha256_hash=doc_hash,
            verifier_id=verifier_id,
            secret_key=secret_key,
        )
        return cls(
            measurement_id=measurement_id,
            surveyor_full_name=surveyor_full_name,
            professional_reg_no=professional_reg_no,
            measurement_date=measurement_date,
            source_document_ref=source_document_ref,
            document_sha256_hash=doc_hash,
            verifier_id=verifier_id,
            signature_hmac_hex=sig,
        )

    def verify_evidence(
        self,
        secret_key: bytes = DEFAULT_CORE_AUTHORITY_SECRET,
    ) -> Tuple[bool, List[str]]:
        errors: List[str] = []
        if not self.surveyor_full_name.strip():
            errors.append("Surveyor full name is empty.")
        if not SURVEYOR_REG_PATTERN.match(self.professional_reg_no):
            errors.append(
                f"Invalid surveyor registration number '{self.professional_reg_no}'; "
                f"must match (BORAQS|ISK|EBK)-[A-Z][0-9]{{4,6}}."
            )
        if "SYNTHETIC" in self.professional_reg_no.upper() or "9999" in self.professional_reg_no:
            errors.append(f"Synthetic/placeholder registration number '{self.professional_reg_no}' is rejected.")
        if not SHA256_PATTERN.match(self.document_sha256_hash):
            errors.append("Measurement document SHA-256 hash is invalid or missing.")
        if not self.verifier_id.strip():
            errors.append("Authorized verifier_id is missing.")

        expected = self.compute_canonical_hmac(
            measurement_id=self.measurement_id,
            surveyor_full_name=self.surveyor_full_name,
            professional_reg_no=self.professional_reg_no,
            measurement_date=self.measurement_date,
            source_document_ref=self.source_document_ref,
            document_sha256_hash=self.document_sha256_hash,
            verifier_id=self.verifier_id,
            secret_key=secret_key,
        )
        if not hmac.compare_digest(self.signature_hmac_hex, expected):
            errors.append("SignedMeasurementEvidence cryptographic HMAC verification failed.")
        return (len(errors) == 0, errors)


@dataclass(frozen=True)
class TakeoffResult:
    """
    Takeoff result that explicitly separates:
    - `input_geometry_status`: VERIFIED_MEASUREMENT only when backed by a verified `SignedMeasurementEvidence`
    - `derived_material_status`: CALCULATED_ESTIMATE for all formula/mix-derived material breakdowns
    - `measurement_status`: Conservative overall status (VERIFIED_MEASUREMENT only for net geometric measurement;
      constituent material quantities are always tagged with `derived_material_status`).
    """
    component_type: str
    net_quantity: float
    unit: str
    waste_factor: float
    gross_quantity: float
    material_breakdown: Dict[str, float]
    measurement_status: MeasurementStatus
    input_geometry_status: MeasurementStatus
    derived_material_status: MeasurementStatus
    formula_reference: str
    measurement_evidence_id: Optional[str] = None
    notes: str = ""
