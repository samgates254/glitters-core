"""
Authoritative Engineering Specification & Professional Registration Verification Controls.
Remediates CRITICAL 1:
- Missing concrete or steel strength values (None) are strictly rejected when minimums are specified.
- Replaces self-declared booleans and unvalidated strings with verifiable `ProfessionalEngineeringApproval`
  tokens authenticated against the GLITTERS CORE registry key, license expiry date, and EBK/BORAQS format.
"""
from __future__ import annotations
from dataclasses import dataclass
import datetime
import hashlib
import hmac
import re
from typing import List, Optional, Tuple


DEFAULT_CORE_AUTHORITY_SECRET = b"GLITTERS-CORE-EBK-BORAQS-AUTHORITY-KEY-2026"
EBK_PATTERN = re.compile(r"^EBK-[A-Z][0-9]{4,6}$")
BORAQS_PATTERN = re.compile(r"^BORAQS-[A-Z][0-9]{4,6}$")
SHA256_HEX_PATTERN = re.compile(r"^[a-fA-F0-9]{64}$")
FORBIDDEN_TOKENS = ("SYNTHETIC", "FAKE", "DUMMY", "TEST", "UNAPPROVED", "PLACEHOLDER", "9999")


@dataclass(frozen=True)
class ProfessionalEngineeringApproval:
    """
    Verifiable professional engineering sign-off record issued by GLITTERS CORE.
    """
    approval_id: str
    engineer_full_name: str
    regulatory_body: str            # "EBK" or "BORAQS"
    registration_number: str        # Must match EBK-A1234 or BORAQS-Q1234
    license_valid_until: datetime.date
    approved_drawing_ref: str
    drawing_sha256_hash: str        # 64-char hex SHA-256 of signed structural drawing PDF/IFC
    approval_date: datetime.date
    signature_hmac_hex: str         # HMAC-SHA256 over canonical approval payload

    @staticmethod
    def compute_canonical_hmac(
        approval_id: str,
        engineer_full_name: str,
        regulatory_body: str,
        registration_number: str,
        license_valid_until: datetime.date,
        approved_drawing_ref: str,
        drawing_sha256_hash: str,
        secret_key: bytes = DEFAULT_CORE_AUTHORITY_SECRET,
    ) -> str:
        payload = (
            f"{approval_id}|{engineer_full_name}|{regulatory_body}|{registration_number}|"
            f"{license_valid_until.isoformat()}|{approved_drawing_ref}|{drawing_sha256_hash.lower()}"
        ).encode("utf-8")
        return hmac.new(secret_key, payload, hashlib.sha256).hexdigest()

    @classmethod
    def create_verified_approval(
        cls,
        approval_id: str,
        engineer_full_name: str,
        regulatory_body: str,
        registration_number: str,
        license_valid_until: datetime.date,
        approved_drawing_ref: str,
        drawing_content_bytes: bytes = b"GLITTERS-APPROVED-STRUCTURAL-DRAWING-V1",
        approval_date: datetime.date = datetime.date(2026, 11, 1),
        secret_key: bytes = DEFAULT_CORE_AUTHORITY_SECRET,
    ) -> ProfessionalEngineeringApproval:
        drawing_hash = hashlib.sha256(drawing_content_bytes).hexdigest()
        sig = cls.compute_canonical_hmac(
            approval_id=approval_id,
            engineer_full_name=engineer_full_name,
            regulatory_body=regulatory_body,
            registration_number=registration_number,
            license_valid_until=license_valid_until,
            approved_drawing_ref=approved_drawing_ref,
            drawing_sha256_hash=drawing_hash,
            secret_key=secret_key,
        )
        return cls(
            approval_id=approval_id,
            engineer_full_name=engineer_full_name,
            regulatory_body=regulatory_body,
            registration_number=registration_number,
            license_valid_until=license_valid_until,
            approved_drawing_ref=approved_drawing_ref,
            drawing_sha256_hash=drawing_hash,
            approval_date=approval_date,
            signature_hmac_hex=sig,
        )

    def verify_authority(
        self,
        reference_date: datetime.date = datetime.date(2026, 11, 12),
        secret_key: bytes = DEFAULT_CORE_AUTHORITY_SECRET,
    ) -> Tuple[bool, List[str]]:
        errors: List[str] = []
        reg_upper = self.registration_number.upper()
        for bad in FORBIDDEN_TOKENS:
            if bad in reg_upper or bad in self.approved_drawing_ref.upper():
                errors.append(
                    f"Registration '{self.registration_number}' or drawing '{self.approved_drawing_ref}' "
                    f"contains forbidden non-production token '{bad}'."
                )
        if self.regulatory_body == "EBK":
            if not EBK_PATTERN.match(self.registration_number):
                errors.append(f"Invalid EBK registration number format: '{self.registration_number}'.")
        elif self.regulatory_body == "BORAQS":
            if not BORAQS_PATTERN.match(self.registration_number):
                errors.append(f"Invalid BORAQS registration number format: '{self.registration_number}'.")
        else:
            errors.append(f"Unsupported regulatory body '{self.regulatory_body}'; must be EBK or BORAQS.")

        if self.license_valid_until < reference_date:
            errors.append(
                f"Engineer license '{self.registration_number}' expired on {self.license_valid_until.isoformat()} "
                f"(reference date {reference_date.isoformat()})."
            )
        if not SHA256_HEX_PATTERN.match(self.drawing_sha256_hash):
            errors.append("Approved drawing SHA-256 hash is missing or malformed.")

        expected_hmac = self.compute_canonical_hmac(
            approval_id=self.approval_id,
            engineer_full_name=self.engineer_full_name,
            regulatory_body=self.regulatory_body,
            registration_number=self.registration_number,
            license_valid_until=self.license_valid_until,
            approved_drawing_ref=self.approved_drawing_ref,
            drawing_sha256_hash=self.drawing_sha256_hash,
            secret_key=secret_key,
        )
        if not hmac.compare_digest(self.signature_hmac_hex, expected_hmac):
            errors.append("Cryptographic HMAC signature verification failed for ProfessionalEngineeringApproval.")

        return (len(errors) == 0, errors)


@dataclass(frozen=True)
class EngineeringSpecification:
    """
    Hard engineering constraint governing structural work packages and activities.
    Remediates CRITICAL 1:
    1. If `min_concrete_class_mpa` is specified, `concrete_mpa=None` is strictly REJECTED.
    2. If `min_steel_yield_mpa` is specified, `steel_mpa=None` is strictly REJECTED.
    3. Requires a verified `ProfessionalEngineeringApproval` token (raw strings/booleans cannot bypass).
    """
    spec_id: str
    structural_code: str
    min_concrete_class_mpa: Optional[float] = None
    min_steel_yield_mpa: Optional[float] = None
    professional_approval: Optional[ProfessionalEngineeringApproval] = None
    # Legacy fields retained for backward error detection (always rejected if used without `professional_approval`)
    approved_drawing_ref: str = "UNAPPROVED"
    licensed_engineer_reg_no: Optional[str] = None
    is_approved_by_licensed_engineer: bool = False

    def validate_compliance(
        self,
        concrete_mpa: Optional[float] = None,
        steel_mpa: Optional[float] = None,
        reference_date: datetime.date = datetime.date(2026, 11, 12),
    ) -> Tuple[bool, List[str]]:
        violations: List[str] = []

        # 1. Verify cryptographic professional engineering approval record
        if self.professional_approval is None:
            violations.append(
                f"Spec {self.spec_id}: Missing verifiable ProfessionalEngineeringApproval record "
                f"(self-declared booleans and unverified registration strings are rejected)."
            )
        else:
            ok_auth, auth_errors = self.professional_approval.verify_authority(reference_date=reference_date)
            if not ok_auth:
                for err in auth_errors:
                    violations.append(f"Spec {self.spec_id}: {err}")

        # 2. Enforce mandatory concrete strength evidence when min_concrete_class_mpa is required
        if self.min_concrete_class_mpa is not None:
            if concrete_mpa is None:
                violations.append(
                    f"Spec {self.spec_id}: Concrete compressive strength is None/unknown, "
                    f"violating mandatory minimum {self.min_concrete_class_mpa} MPa."
                )
            elif concrete_mpa < self.min_concrete_class_mpa:
                violations.append(
                    f"Spec {self.spec_id}: Concrete strength {concrete_mpa} MPa violates "
                    f"minimum {self.min_concrete_class_mpa} MPa."
                )

        # 3. Enforce mandatory steel yield strength evidence when min_steel_yield_mpa is required
        if self.min_steel_yield_mpa is not None:
            if steel_mpa is None:
                violations.append(
                    f"Spec {self.spec_id}: Steel yield strength is None/unknown, "
                    f"violating mandatory minimum {self.min_steel_yield_mpa} MPa."
                )
            elif steel_mpa < self.min_steel_yield_mpa:
                violations.append(
                    f"Spec {self.spec_id}: Steel yield strength {steel_mpa} MPa violates "
                    f"minimum {self.min_steel_yield_mpa} MPa."
                )

        return (len(violations) == 0, violations)
