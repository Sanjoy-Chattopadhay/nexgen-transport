from pydantic import BaseModel
from typing import Optional


class RouteSummaryOut(BaseModel):
    id: int
    origin: Optional[str] = None
    destination: Optional[str] = None
    route_name: Optional[str] = None
    trip_count: int = 0
    avg_duration_min: Optional[float] = None
    eta_success_rate: Optional[float] = None
    avg_distance_km: Optional[float] = None


class RouteTimePatternOut(BaseModel):
    origin: Optional[str] = None
    destination: Optional[str] = None
    hour_of_day: Optional[int] = None
    day_of_week: Optional[int] = None
    avg_duration: Optional[float] = None
    trip_count: int = 0
    eta_success_rate: Optional[float] = None
