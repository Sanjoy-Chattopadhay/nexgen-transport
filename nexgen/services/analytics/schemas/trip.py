from pydantic import BaseModel
from typing import Optional
from datetime import datetime


class TripOut(BaseModel):
    id: int
    dispatch_entry_no: str
    driver_id: Optional[int] = None
    driver_name: Optional[str] = None
    vehicle_id: Optional[int] = None
    asset_id: Optional[str] = None
    origin_id: Optional[int] = None
    origin_name: Optional[str] = None
    destination_id: Optional[int] = None
    destination_name: Optional[str] = None
    customer_id: Optional[int] = None
    trip_start: Optional[datetime] = None
    trip_end: Optional[datetime] = None
    trip_eta: Optional[datetime] = None
    trip_km: Optional[float] = None
    trip_duration_minutes: Optional[float] = None
    eta_met: Optional[bool] = None
    eta_delay_minutes: Optional[float] = None
    avg_speed_kmph: Optional[float] = None
    trip_status: Optional[str] = None
    is_active: Optional[str] = None
    trip_closed_at: Optional[datetime] = None
    trip_close_remark: Optional[str] = None
    material_desc: Optional[str] = None


class TripStatsOut(BaseModel):
    total_trips: int
    completed_trips: int
    active_trips: int
    avg_duration_minutes: Optional[float] = None
    avg_speed_kmph: Optional[float] = None
    eta_success_rate: Optional[float] = None
