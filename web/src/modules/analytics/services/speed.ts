// Speed & safety API client.
//
// Every call takes the shared analytics filter params plus the two speed
// limits. The limits are never defaulted here: the backend owns the business
// defaults (60 km/h on the road, 20 inside a plant) and echoes back the ones it
// used, so a screen renders the limit it was actually judged against rather
// than the one it thinks it asked for.
import { backendApi } from './api';

type P = Record<string, string>;

export interface SpeedLimits {
  road: number;
  plant: number;
  road_options?: number[];
  plant_options?: number[];
  road_default?: number;
  plant_default?: number;
  detection_floors?: Record<string, number>;
}

export interface SpeedBin {
  kmph: number;
  pings: number;
  minutes: number | null;
  /** true when this speed breaches the limit currently applied to the zone */
  over: boolean;
}

export interface PlantFences {
  count: number;
  names: string[];
  radius_km: number | null;
}

export interface ZoneBlock {
  label: string;
  limit_kmph: number;
  detection_floor_kmph: number;
  /** Present on the plant zone only: which fences define "inside", and how big
   *  they are. The radius is what the in-plant numbers actually mean. */
  fences: PlantFences | null;
  pings: number;
  minutes: number | null;
  dist_km: number | null;
  moving_minutes: number | null;
  avg_moving_kmph: number | null;
  p50_kmph: number | null;
  p85_kmph: number | null;
  p95_kmph: number | null;
  max_kmph: number | null;
  over_minutes: number | null;
  over_dist_km: number | null;
  over_pings: number;
  over_pct_of_moving_time: number | null;
  episodes: number;
  episode_trips: number;
  episode_vehicles: number;
  worst_peak_kmph: number | null;
  histogram: SpeedBin[];
}

export interface SpeedOverview {
  limits: SpeedLimits;
  coverage: {
    trips_in_filter: number;
    trips_with_gps: number;
    gps_coverage_pct: number | null;
    days: number;
    observed_max_kmph: number | null;
  };
  zones: { plant: ZoneBlock; road: ZoneBlock };
  totals: {
    episodes: number;
    episodes_per_day: number | null;
    episodes_per_100_trips: number | null;
    over_minutes: number | null;
    over_dist_km: number | null;
  };
  build: BuildStatus;
}

export interface BuildStatus {
  built: boolean;
  built_at: string | null;
  gps_pings: number;
  pings_covered: number;
  pings_behind: number;
  stale: boolean;
  event_floors?: Record<string, number>;
  rollups: Record<string, {
    rows: number; trips: number; seconds: number;
    built_at: string | null; detail: unknown;
  }>;
}

export interface SafetyDay {
  date: string;
  /** Trips ON THE ROAD that day — the denominator the rate uses, because a
   *  violation can come from a trip that departed days earlier. */
  active_trips: number;
  active_vehicles: number;
  /** Trips that left the plant that day. Context, not a denominator. */
  departures: number;
  episodes: number;
  plant_episodes: number;
  road_episodes: number;
  episode_minutes: number;
  episode_dist_km: number;
  offending_vehicles: number;
  offending_trips: number;
  worst_peak_kmph: number | null;
  episodes_per_100_trips: number | null;
  episodes_per_vehicle: number | null;
}

export interface SafetyDaily {
  limits: SpeedLimits;
  series: SafetyDay[];
  summary: {
    days: number;
    episodes: number;
    avg_per_day: number | null;
    worst_day: string | null;
    worst_day_episodes: number;
    clean_days: number;
    episode_minutes: number;
    episode_dist_km: number;
    offending_vehicles: number;
  };
  worst_offenders: {
    vehicle_no: string; transporter: string; episodes: number; trips: number;
    episode_minutes: number; peak_kmph: number; plant_episodes: number;
  }[];
}

export interface SpeedEvent {
  trip_id: number;
  transporter: string | null;
  vehicle_no: string | null;
  driver_name: string | null;
  origin: string | null;
  destination: string | null;
  zone: 'plant' | 'road';
  detected_above_kmph: number;
  peak_kmph: number;
  avg_kmph: number | null;
  episode_minutes: number | null;
  dist_km: number | null;
  pings: number;
  start: string | null;
  end: string | null;
  lat: number | null;
  lon: number | null;
  date: string | null;
}

export interface RunningHour {
  hour: number;
  label: string;
  moving_hours: number | null;
  stopped_hours: number | null;
  in_plant_hours: number | null;
  running_pct: number | null;
  in_plant_pct: number | null;
  dist_km: number | null;
  avg_kmph: number | null;
  max_kmph: number | null;
  vehicles: number;
  trips: number;
}

export interface RunningPattern {
  transporter: string | null;
  trips: number;
  hours: RunningHour[];
  summary: {
    peak_hour: string | null;
    peak_running_pct: number | null;
    quiet_hour: string | null;
    quiet_running_pct: number | null;
    avg_running_pct: number | null;
    total_moving_hours: number | null;
    total_stopped_hours: number | null;
    total_in_plant_hours: number | null;
    night_share_pct: number | null;
    night_moving_hours: number | null;
    night_window: string | null;
  };
}

export interface CarrierSafetyRow {
  transporter: string;
  trips: number;
  vehicles: number;
  days: number;
  gps_trips: number;
  gps_coverage_pct: number | null;
  episodes: number;
  road_episodes: number;
  plant_episodes: number;
  peak_kmph: number | null;
  offending_vehicles: number;
  offending_trips: number;
  avg_moving_kmph: number | null;
  over_minutes: number;
  over_dist_km: number;
  over_pct_of_moving_time: number | null;
  moving_hours: number;
  plant_hours: number;
  max_kmph: number | null;
  episodes_per_100_trips: number | null;
  episodes_per_day: number | null;
}

const limitParams = (road: number, plant: number) => ({
  road_limit: String(road), plant_limit: String(plant),
});

export const getSpeedOverview = (road: number, plant: number, p: P) =>
  backendApi.get<SpeedOverview>('/speed/overview',
    { params: { ...limitParams(road, plant), ...p } });

export const getSafetyDaily = (road: number, plant: number, p: P) =>
  backendApi.get<SafetyDaily>('/speed/daily',
    { params: { ...limitParams(road, plant), ...p } });

export const getSpeedEvents = (
  road: number, plant: number, p: P,
  opts: { zone?: string; transporter?: string; rows?: number } = {},
) =>
  backendApi.get<{ total: number; returned: number; rows: SpeedEvent[]; limits: SpeedLimits }>(
    '/speed/events', {
      params: {
        ...limitParams(road, plant),
        ...(opts.zone ? { zone: opts.zone } : {}),
        ...(opts.transporter ? { transporter: opts.transporter } : {}),
        rows: String(opts.rows ?? 300),
        ...p,
      },
    });

export const getCarrierSafety = (road: number, plant: number, minTrips: number, p: P) =>
  backendApi.get<{ limits: SpeedLimits; min_trips: number; rows: CarrierSafetyRow[] }>(
    '/speed/carriers',
    { params: { ...limitParams(road, plant), min_trips: String(minTrips), ...p } });

export const getRunningPattern = (p: P, transporter?: string) =>
  backendApi.get<RunningPattern>('/speed/running-pattern',
    { params: { ...(transporter ? { transporter } : {}), ...p } });

export const getSpeedStatus = () => backendApi.get<BuildStatus>('/speed/status');

/** Operator action: rescans the ping table, minutes on the full corpus. */
export const rebuildSpeed = () => backendApi.post<Record<string, unknown>>('/speed/rebuild', {});
