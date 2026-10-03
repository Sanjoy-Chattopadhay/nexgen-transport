from pydantic import BaseModel
from typing import Optional, List
from datetime import date


class FleetSummary(BaseModel):
    total_trips: int
    total_drivers: int
    total_vehicles: int
    total_distance_km: Optional[float] = None
    avg_speed_kmph: Optional[float] = None
    eta_success_rate: Optional[float] = None


class DailyTrend(BaseModel):
    stat_date: date
    total_trips: int
    total_distance_km: Optional[float] = None
    avg_speed: Optional[float] = None
    eta_success_rate: Optional[float] = None
    active_drivers: int = 0
    active_vehicles: int = 0


class AlertOut(BaseModel):
    id: int
    alert_type: str
    severity: str
    title: str
    message: Optional[str] = None
    trip_id: Optional[int] = None
    driver_id: Optional[int] = None
    vehicle_id: Optional[int] = None
    is_acknowledged: bool = False
    created_at: Optional[str] = None
