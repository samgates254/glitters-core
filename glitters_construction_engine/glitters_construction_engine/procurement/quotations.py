"""
Supplier Quotation Evidence Model.
Remediates CRITICAL 3:
- Replaces `MeasurementStatus` on quotations with a dedicated `QuotationVerificationStatus` enum.
- Defaults to `UNVERIFIED_DRAFT_QUOTATION` (never `VERIFIED_MEASUREMENT`).
- Tracks quotation reference, issue date, expiry date, material standard, SHA-256 document hash,
  and cryptographic verification status.
"""
from __future__ import annotations
from dataclasses import dataclass
import datetime
from enum import Enum
import hashlib
import re
from typing import List, Tuple

SHA256_HEX_RE = re.compile(r"^[a-fA-F0-9]{64}$")


class QuotationVerificationStatus(str, Enum):
    VERIFIED_BINDING_QUOTATION = "VERIFIED_BINDING_QUOTATION"
    UNVERIFIED_DRAFT_QUOTATION = "UNVERIFIED_DRAFT_QUOTATION"
    EXPIRED_QUOTATION = "EXPIRED_QUOTATION"
    SYNTHETIC_BENCHMARK_QUOTATION = "SYNTHETIC_BENCHMARK_QUOTATION"


@dataclass(frozen=True)
class SupplierQuotation:
    supplier_id: str
    supplier_name: str
    material_id: str
    county: str
    unit_price_kes: float
    min_order_quantity: float
    max_supply_capacity: float
    lead_time_days: int
    reliability_score: float
    fixed_delivery_fee_kes: float
    quotation_ref: str = "UNSPECIFIED-QUOTE-REF"
    issue_date: datetime.date = datetime.date(2026, 11, 1)
    expiry_date: datetime.date = datetime.date(2026, 12, 31)
    material_spec_standard: str = "KS EAS 18-1:2017 / KS EAS 412-2:2019"
    document_sha256_hash: str = ""
    verification_status: QuotationVerificationStatus = QuotationVerificationStatus.UNVERIFIED_DRAFT_QUOTATION

    @classmethod
    def create_verified_quotation(
        cls,
        supplier_id: str,
        supplier_name: str,
        material_id: str,
        county: str,
        unit_price_kes: float,
        min_order_quantity: float,
        max_supply_capacity: float,
        lead_time_days: int,
        reliability_score: float,
        fixed_delivery_fee_kes: float,
        quotation_ref: str,
        issue_date: datetime.date = datetime.date(2026, 11, 1),
        expiry_date: datetime.date = datetime.date(2026, 12, 31),
        material_spec_standard: str = "KS EAS 18-1:2017",
        document_bytes: bytes = b"SIGNED-SUPPLIER-PROFORMA-QUOTATION",
    ) -> SupplierQuotation:
        return cls(
            supplier_id=supplier_id,
            supplier_name=supplier_name,
            material_id=material_id,
            county=county,
            unit_price_kes=unit_price_kes,
            min_order_quantity=min_order_quantity,
            max_supply_capacity=max_supply_capacity,
            lead_time_days=lead_time_days,
            reliability_score=reliability_score,
            fixed_delivery_fee_kes=fixed_delivery_fee_kes,
            quotation_ref=quotation_ref,
            issue_date=issue_date,
            expiry_date=expiry_date,
            material_spec_standard=material_spec_standard,
            document_sha256_hash=hashlib.sha256(document_bytes).hexdigest(),
            verification_status=QuotationVerificationStatus.VERIFIED_BINDING_QUOTATION,
        )

    def is_valid_for_production_procurement(
        self,
        reference_date: datetime.date = datetime.date(2026, 11, 12),
    ) -> Tuple[bool, List[str]]:
        reasons: List[str] = []
        if self.verification_status != QuotationVerificationStatus.VERIFIED_BINDING_QUOTATION:
            reasons.append(
                f"Quotation '{self.quotation_ref}' from '{self.supplier_id}' has status "
                f"{self.verification_status.value} (must be VERIFIED_BINDING_QUOTATION for production procurement)."
            )
        if "SYNTHETIC" in self.supplier_name.upper() or "SYNTHETIC" in self.quotation_ref.upper():
            reasons.append(f"Quotation '{self.quotation_ref}' is marked SYNTHETIC and cannot be used in production.")
        if self.expiry_date < reference_date:
            reasons.append(
                f"Quotation '{self.quotation_ref}' expired on {self.expiry_date.isoformat()} "
                f"(reference date {reference_date.isoformat()})."
            )
        if self.issue_date > reference_date:
            reasons.append(f"Quotation '{self.quotation_ref}' has future issue date {self.issue_date.isoformat()}.")
        if not SHA256_HEX_RE.match(self.document_sha256_hash):
            reasons.append(f"Quotation '{self.quotation_ref}' lacks a valid 64-char SHA-256 document hash.")
        if self.unit_price_kes <= 0:
            reasons.append(f"Quotation '{self.quotation_ref}' has non-positive unit price ({self.unit_price_kes}).")
        return (len(reasons) == 0, reasons)
