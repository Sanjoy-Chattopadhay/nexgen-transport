export interface TripRow {
  id: number;
  dispatch_entry_no: string;
  driver_name: string;
  asset_id: string;
  origin_name: string;
  destination_name: string;
  trip_start: string;
  trip_end: string;
  trip_duration_minutes: number;
  eta_met: boolean;
  eta_delay_minutes: number;
  avg_speed_kmph: number;
  trip_km: number;
  trip_status: string;
  is_active: string;
  material_desc: string;
}

export interface TripStats {
  total_trips: number;
  completed_trips: number;
  active_trips: number;
  avg_duration_minutes: number | null;
  avg_speed_kmph: number | null;
  eta_success_rate: number | null;
}

export interface Waypoint {
  latitude: number | null;
  longitude: number | null;
  speed_kmph: number | null;
  status: string | null;
  location_text: string | null;
  distance_from_prev: number | null;
  recorded_at: string;
}

export interface DriverStats {
  driver_id: number;
  driver_name: string;
  driver_mobile: string;
  total_trips: number;
  eta_met_count: number;
  eta_success_rate: number | null;
  avg_duration_min: number;
  avg_speed_kmph: number;
  total_distance_km: number;
  avg_distance_km: number;
  vehicles_used: number;
  avg_eta_delay_min: number;
}

export interface RouteStats {
  origin: string;
  destination: string;
  route_name: string;
  trip_count: number;
  avg_duration_min: number;
  avg_speed_kmph: number;
  eta_success_rate: number | null;
  avg_distance_km: number;
}

export interface VehicleStats {
  vehicle_id: number;
  asset_id: string;
  asset_type: string;
  total_trips: number;
  drivers_used: number;
  avg_speed_kmph: number;
  total_distance_km: number;
  avg_distance_km: number;
  eta_success_rate: number | null;
}

export interface DriverRouteStats {
  route_trips: number;
  avg_duration_min: number;
  avg_speed_kmph: number;
  eta_success_rate: number | null;
  avg_distance_km: number;
}

export interface RouteRecentTrip {
  id: number;
  dispatch_entry_no: string;
  driver_name: string;
  trip_start: string;
  trip_duration_minutes: number;
  avg_speed_kmph: number;
  eta_met: boolean;
  trip_km: number;
}

export interface TripDetail {
  trip: TripRow & {
    driver_id: number;
    driver_mobile: string;
    vehicle_id: number;
    asset_type: string;
    origin_id: number;
    destination_id: number;
    customer_id: number;
    customer_name: string;
    trip_eta: string;
    trip_close_remark: string;
  };
  waypoints: Waypoint[];
  driver_stats: DriverStats | null;
  route_stats: RouteStats | null;
  vehicle_stats: VehicleStats | null;
  driver_route_stats: DriverRouteStats | null;
  route_recent_trips: RouteRecentTrip[];
}
