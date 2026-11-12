"""
Precedence Dependency Types, Measurement Provenance Enums, and Kenyan County Work Calendars.
"""
from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
from typing import Tuple
import datetime


class ProjectArchetype(str, Enum):
    RESIDENTIAL = "RESIDENTIAL"
    COMMERCIAL = "COMMERCIAL"
    INDUSTRIAL = "INDUSTRIAL"
    APARTMENT_COMPLEX = "APARTMENT_COMPLEX"
    INFRASTRUCTURE = "INFRASTRUCTURE"
    ROADS_CIVIL = "ROADS_CIVIL"
    RENOVATION = "RENOVATION"
    BUILDING_EXTENSION = "BUILDING_EXTENSION"
    MIXED_USE = "MIXED_USE"
    LARGE_SCALE_PROPERTY = "LARGE_SCALE_PROPERTY"


class DependencyType(str, Enum):
    FS = "FS"
    SS = "SS"
    FF = "FF"
    SF = "SF"


class ResourceCategory(str, Enum):
    HUMAN = "HUMAN"
    EQUIPMENT = "EQUIPMENT"
    MATERIAL = "MATERIAL"


class MeasurementStatus(str, Enum):
    VERIFIED_MEASUREMENT = "VERIFIED_MEASUREMENT"
    CALCULATED_ESTIMATE = "CALCULATED_ESTIMATE"
    SYNTHETIC_BENCHMARK = "SYNTHETIC_BENCHMARK"
    UNVERIFIED_ASSUMPTION = "UNVERIFIED_ASSUMPTION"


class RateProvenanceStatus(str, Enum):
    VERIFIED_CONTRACT_RATE = "VERIFIED_CONTRACT_RATE"
    VERIFIED_SUPPLIER_QUOTATION = "VERIFIED_SUPPLIER_QUOTATION"
    DERIVED_RATE_ESTIMATE = "DERIVED_RATE_ESTIMATE"
    SYNTHETIC_BENCHMARK_RATE = "SYNTHETIC_BENCHMARK_RATE"
    UNVERIFIED_RATE_ASSUMPTION = "UNVERIFIED_RATE_ASSUMPTION"


@dataclass(frozen=True)
class PrecedenceDependency:
    predecessor_id: str
    successor_id: str
    dep_type: DependencyType = DependencyType.FS
    lag_days: int = 0


@dataclass(frozen=True)
class KenyanWorkCalendar:
    """
    Kenyan construction work calendar mapping integer working-day indices to real calendar dates
    while skipping weekends, statutory Kenyan public holidays, and county weather shutdowns.
    """
    calendar_id: str
    county: str = "Nairobi"
    working_days_per_week: int = 6
    hours_per_day: float = 8.0
    public_holidays: Tuple[datetime.date, ...] = (
        datetime.date(2026, 1, 1),   # New Year's Day
        datetime.date(2026, 5, 1),   # Labour Day
        datetime.date(2026, 6, 1),   # Madaraka Day
        datetime.date(2026, 10, 10), # Mazingira Day
        datetime.date(2026, 10, 20), # Mashujaa Day
        datetime.date(2026, 12, 12), # Jamhuri Day
        datetime.date(2026, 12, 25), # Christmas Day
        datetime.date(2026, 12, 26), # Boxing Day
    )
    non_working_dates: Tuple[datetime.date, ...] = ()

    def is_working_day(self, date_obj: datetime.date) -> bool:
        if date_obj in self.public_holidays or date_obj in self.non_working_dates:
            return False
        if self.working_days_per_week == 5 and date_obj.weekday() >= 5:
            return False
        if self.working_days_per_week == 6 and date_obj.weekday() == 6:
            return False
        return True

    def working_day_index_to_date(self, project_start_date: datetime.date, working_day_index: int) -> datetime.date:
        current = project_start_date
        while not self.is_working_day(current):
            current += datetime.timedelta(days=1)
        remaining = max(0, int(working_day_index))
        while remaining > 0:
            current += datetime.timedelta(days=1)
            if self.is_working_day(current):
                remaining -= 1
        return current

    def add_working_days(self, start_date: datetime.date, duration_days: int) -> datetime.date:
        return self.working_day_index_to_date(start_date, duration_days)

    def working_days_between(self, start_date: datetime.date, end_date: datetime.date) -> int:
        if end_date <= start_date:
            return 0
        cur = start_date
        count = 0
        while cur < end_date:
            if self.is_working_day(cur):
                count += 1
            cur += datetime.timedelta(days=1)
        return count
