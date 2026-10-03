export interface MLModel {
  id: number;
  model_name: string;
  version: number;
  model_type: string;
  target_variable: string;
  metrics: Record<string, number>;
  feature_columns?: string[];
  training_data_count: number;
  is_active: number;
  trained_at: string | null;
}

export interface ETAPredictRequest {
  origin: string;
  destination: string;
  driver_id?: number;
  vehicle_id?: number;
  trip_km?: number;
  trip_start?: string;
}

export interface SLAPredictRequest {
  origin: string;
  destination: string;
  driver_id?: number;
  vehicle_id?: number;
  trip_km?: number;
  trip_start?: string;
}

export interface DriverRecommendRequest {
  origin: string;
  destination: string;
  top_n?: number;
}

export interface RouteOptimizeRequest {
  origin: string;
  destination: string;
  trip_km?: number;
  hour?: number;
  day_of_week?: number;
}

// SLA Prediction result
export interface SLAResult {
  on_time_probability: number;
  prediction: string;
  risk_level: string; // low | medium | high | critical
  contributing_factors: Record<string, number>;
}

// Fatigue
export interface FatigueDriver {
  driver_id: number;
  driver_name: string;
  fatigue_score: number;
  risk_level: string;
  hours_driving_24h: number;
  hours_driving_7d: number;
  consecutive_days: number;
  night_trips_ratio: number;
  factors: Record<string, any>;
}

export interface FleetFatigueResult {
  summary: {
    critical: number;
    high: number;
    medium: number;
    low: number;
    total_drivers: number;
  };
  drivers: FatigueDriver[];
}

// Anomaly scan
export interface AnomalyScanResult {
  scanned_trips: number;
  anomalies_found: number;
  alerts_created: number;
  severity_breakdown: Record<string, number>;
  scan_days: number;
}

// Client demand
export interface ClientListItem {
  client: string;
  total_trips: number;
  avg_trips_per_week: number;
  active_weeks: number;
  has_forecast: boolean;
  trend: string;
}

export interface ClientForecastDay {
  date: string;
  day_of_week: string;
  predicted_trips: number;
}

export interface ClientForecast {
  client: string;
  forecast: {
    historical_avg_daily: number;
    recent_avg_daily_7d: number;
    trend: string;
    growth_pct_30d: number;
    next_7_days: ClientForecastDay[];
    total_predicted_week: number;
    total_historical_trips: number;
  };
  generated_at: string;
}

export interface ClientProfileData {
  total_trips: number;
  avg_trips_per_week: number;
  first_trip: string;
  last_trip: string;
  active_weeks: number;
  top_routes: { origin: string; destination: string; trips: number }[];
  day_of_week_pattern: Record<string, number>;
  monthly_trend: Record<string, number>;
}

export interface ClientProfile {
  client: string;
  profile: ClientProfileData;
  forecast: ClientForecast['forecast'] | null;
}
