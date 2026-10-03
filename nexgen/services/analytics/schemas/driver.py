from pydantic import BaseModel
from typing import Optional
from decimal import Decimal


class DriverOut(BaseModel):
    id: int
    name: str
    mobile1: Optional[str] = None
    mobile2: Optional[str] = None


class DriverSummaryOut(BaseModel):
    driver_id: int
    driver_name: Optional[str] = None
    driver_mobile: Optional[str] = None
    total_trips: int = 0
    eta_met_count: int = 0
    # Denominator of eta_success_rate: trips with a KNOWN outcome. Differs
    # from total_trips, which also counts trips that never arrived.
    eta_known_count: Optional[int] = None
    eta_success_rate: Optional[float] = None
    avg_duration_min: Optional[float] = None
    max_duration_min: Optional[float] = None
    min_duration_min: Optional[float] = None
    avg_speed_kmph: Optional[float] = None
    vehicles_used: int = 0
    total_distance_km: Optional[float] = None
    avg_distance_km: Optional[float] = None
    avg_eta_delay_min: Optional[float] = None
