export interface DriverSummaryRow {
  driver_id: number;
  driver_name: string;
  driver_mobile: string;
  total_trips: number;
  eta_met_count: number;
  // Denominator of eta_success_rate: trips with a KNOWN outcome. Lower than
  // total_trips whenever some trips never arrived.
  eta_known_count: number | null;
  eta_success_rate: number | null;
  avg_duration_min: number;
  max_duration_min: number;
  min_duration_min: number;
  avg_speed_kmph: number;
  vehicles_used: number;
  total_distance_km: number;
  avg_distance_km: number;
  avg_eta_delay_min: number;
}

export interface DriverTrip {
  id: number;
  dispatch_entry_no: string;
  origin_name: string;
  destination_name: string;
  trip_start: string;
  trip_end: string;
  trip_duration_minutes: number;
  eta_met: boolean;
  avg_speed_kmph: number;
  trip_km: number;
  trip_status: string;
  asset_id: string;
}

export interface DriverVehicle {
  id: number;
  asset_id: string;
  asset_type: string;
  trip_count: number;
}

export interface DriverRoute {
  origin: string;
  destination: string;
  trip_count: number;
}

export interface DriverTrend {
  period: string;
  trip_count: number;
  avg_duration: number;
  eta_success_rate: number | null;
  avg_speed: number;
  avg_distance: number;
  avg_delay: number;
}

export interface DriverDetail {
  summary: DriverSummaryRow;
  recent_trips: DriverTrip[];
  vehicles_used: DriverVehicle[];
  frequent_routes: DriverRoute[];
}

export interface HourlyPattern {
  hour_of_day: number;
  avg_speed: number;
  max_speed: number;
  dist_km: number;
  distance_share_pct: number;
  moving_pct: number;
  data_points: number;
}

export interface SpeedDistribution {
  speed_range: string;
  count: number;
}

export interface DailyPattern {
  day_num: number;
  day_name: string;
  avg_speed: number;
  dist_km: number;
  data_points: number;
}

export interface DrivingPatternStats {
  total_points: number;
  total_days: number;
  overall_avg_speed: number;
  top_speed: number;
  total_distance_tracked: number;
  moving_pct: number;
}

export interface DrivingPattern {
  hourly_pattern: HourlyPattern[];
  speed_distribution: SpeedDistribution[];
  daily_pattern: DailyPattern[];
  stats: DrivingPatternStats | null;
  trip_count: number;
  window: string;
}

export interface DriverAlert {
  alert_type: string;
  severity: 'critical' | 'warning' | 'info';
  title: string;
  message: string;
  trip_no: number;
  at: string | null;
  metadata: Record<string, any>;
}

export interface DriverAlerts {
  alerts: DriverAlert[];
  summary: {
    total: number;
    by_type: Record<string, number>;
    by_severity: Record<string, number>;
    safety_score: number;
  };
  timeline: { date: string; count: number }[];
  trip_count: number;
  window: string;
}

export interface DriverScoreData {
  driver_id: number;
  composite_score: number;
  scores: Record<string, any>;
  scored_at?: string;
}
