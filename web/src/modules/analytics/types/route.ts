export interface RouteSummaryRow {
  id: number;
  origin: string;
  destination: string;
  route_name: string;
  trip_count: number;
  avg_duration_min: number;
  eta_success_rate: number | null;
  avg_distance_km: number;
}

export interface RouteTimePattern {
  hour_of_day: number;
  day_of_week: number;
  avg_duration: number;
  trip_count: number;
  eta_success_rate: number | null;
}

export interface RouteTopDriver {
  driver_id: number;
  driver_name: string;
  trip_count: number;
  avg_duration: number;
  eta_rate: number | null;
}

export interface RouteDetail {
  summary: RouteSummaryRow;
  time_patterns: RouteTimePattern[];
  top_drivers: RouteTopDriver[];
  recent_trips: Array<{
    id: number;
    dispatch_entry_no: string;
    driver_name: string;
    asset_id: string;
    trip_start: string;
    trip_duration_minutes: number;
    eta_met: boolean;
    avg_speed_kmph: number;
  }>;
}
