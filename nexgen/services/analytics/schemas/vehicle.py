from pydantic import BaseModel
from typing import Optional


class VehicleOut(BaseModel):
    id: int
    asset_id: str
    asset_type: Optional[str] = None
    trailer_type: Optional[str] = None


class VehicleSummaryOut(BaseModel):
    vehicle_id: int
    asset_id: Optional[str] = None
    asset_type: Optional[str] = None
    total_trips: int = 0
    drivers_used: int = 0
    avg_speed_kmph: Optional[float] = None
    total_distance_km: Optional[float] = None
    avg_distance_km: Optional[float] = None
    eta_success_rate: Optional[float] = None
