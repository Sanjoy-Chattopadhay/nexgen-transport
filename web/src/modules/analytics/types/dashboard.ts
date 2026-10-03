export interface FleetSummary {
  total_trips: number;
  total_drivers: number;
  total_vehicles: number;
  total_distance_km: number | null;
  avg_speed_kmph: number | null;
  eta_success_rate: number | null;
}

export interface DailyTrend {
  stat_date: string;
  total_trips: number;
  total_distance_km: number | null;
  avg_speed: number | null;
  eta_success_rate: number | null;
  active_drivers: number;
  active_vehicles: number;
}

export interface AlertOut {
  id: number;
  alert_type: string;
  severity: string;
  title: string;
  message: string | null;
  trip_id: number | null;
  driver_id: number | null;
  vehicle_id: number | null;
  is_acknowledged: boolean;
  created_at: string | null;
}

export interface TopDriver {
  driver_id: number;
  driver_name: string;
  driver_mobile: string;
  total_trips: number;
  eta_success_rate: number | null;
  avg_speed_kmph: number;
  total_distance_km: number;
}

export interface RouteHeatmapItem {
  origin: string;
  destination: string;
  route_name: string;
  trip_count: number;
  avg_duration_min: number;
  eta_success_rate: number | null;
  avg_distance_km: number;
}
