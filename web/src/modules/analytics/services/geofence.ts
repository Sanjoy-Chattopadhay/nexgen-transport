import { backendApi } from './api';

/**
 * Geofence module: works detention measured against the origin fence, and
 * GPS-coverage reporting.
 *
 * Consignor scoping is applied transparently by the backendApi interceptor —
 * these calls never pass a consignor id themselves.
 */

export type TripClass = 'zonal' | 'local' | 'all';

// ---------------------------------------------------------------- detention

export interface StatSummary {
  n: number;
  p10: number | null;
  p25: number | null;
  median: number | null;
  p75: number | null;
  p90: number | null;
  mean: number | null;
}

export interface DetentionSummary {
  trip_class: string | null;
  trips_with_confirmed_exit: number;
  declared_works_detention_h: StatSummary;
  hidden_tail_h: StatSummary;
  true_total_detention_h: StatSummary;
  tail_over_4h: number;
  tail_over_12h: number;
  excluded: {
    wide_gps_gap: number;
    negative_tail_late_stamp: number;
    note: string;
  };
}

export interface DetentionRow {
  transporter?: string;
  destination?: string;
  trips: number;
  declared_median_h: number | null;
  hidden_tail_median_h: number | null;
  hidden_tail_p90_h: number | null;
  understatement_pct: number | null;
}

export const getDetentionSummary = (tripClass: TripClass = 'zonal') =>
  backendApi.get<DetentionSummary>('/geofence/detention', { params: { trip_class: tripClass } });

export const getDetentionBy = (dimension: 'transporter' | 'destination', tripClass: TripClass = 'zonal') =>
  backendApi.get<DetentionRow[]>(`/geofence/detention/by/${dimension}`, {
    params: { trip_class: tripClass },
  });

// -------------------------------------------------------------- GPS quality

export interface GpsScore {
  trips: number;
  gps_ok_pct: number | null;
  silent_trips: number;
  silent_pct: number | null;
  died_at_origin: number;
  died_at_origin_pct: number | null;
  late_start: number;
  gappy: number;
  gps_on_time_pct: number | null;
  median_ping_count: number;
}

export interface TransporterGpsRow extends GpsScore { transporter: string }
export interface RegionGpsRow extends GpsScore { region: string }

export interface RouteEndGapRow {
  destination: string;
  trips: number;
  median_end_gap_km: number;
  p90_end_gap_km: number;
  arrived_pct: number | null;
  stopped_over_50km_short: number;
  no_destination_fence_trips: number;
  fence_source: string;
  /** False when the fence is a town centroid and the gap is small — see the
   *  backend note: that means "we don't know exactly where this customer is",
   *  not "the truck fell short". */
  gap_is_conclusive: boolean;
}

export interface UngeofencedFinding {
  destination: string;
  trips_on_lane: number;
  trips_ran_full_route_no_geofence_close: number;
  median_lane_distance_km: number;
  close_reasons: string[];
  suggested_fence_centre: { lat: number; lon: number } | null;
  derived_fence_already_available: boolean;
}

export interface UngeofencedReport {
  trip_class: string | null;
  criteria: { distance_threshold: string; min_lane_trips: number };
  destinations_needing_a_geofence: number;
  trips_affected: number;
  findings: UngeofencedFinding[];
}

export const getGpsSummary = (tripClass: TripClass = 'zonal') =>
  backendApi.get<GpsScore>('/geofence/gps/summary', { params: { trip_class: tripClass } });

export const getGpsTransporters = (tripClass: TripClass = 'zonal', minTrips = 5) =>
  backendApi.get<TransporterGpsRow[]>('/geofence/gps/transporters', {
    params: { trip_class: tripClass, min_trips: minTrips },
  });

export const getGpsRegions = (tripClass: TripClass = 'zonal', minTrips = 5) =>
  backendApi.get<RegionGpsRow[]>('/geofence/gps/regions', {
    params: { trip_class: tripClass, min_trips: minTrips },
  });

export const getRouteEndGap = (tripClass: TripClass = 'zonal', minTrips = 5) =>
  backendApi.get<RouteEndGapRow[]>('/geofence/gps/route-end-gap', {
    params: { trip_class: tripClass, min_trips: minTrips },
  });

export const getUngeofenced = (tripClass: TripClass = 'zonal') =>
  backendApi.get<UngeofencedReport>('/geofence/gps/ungeofenced', {
    params: { trip_class: tripClass },
  });

// ------------------------------------------------------------------ status

export interface GeofenceStatus {
  by_trip_class: Record<string, Record<string, number>>;
  status_meaning: Record<string, string>;
}

export const getGeofenceStatus = () => backendApi.get<GeofenceStatus>('/geofence/status');

export interface FenceRow {
  fence_id: number;
  key: string;
  name: string;
  role: string;
  lat: number;
  lon: number;
  radius_m: number;
}

export const getFences = () =>
  backendApi.get<{ index: Record<string, number>; fences: FenceRow[] }>('/geofence/fences');

// ------------------------------------------- single-trip proof, for the map

export interface DemoTrip {
  trip_no: number;
  origin: string;
  destination: string;
  transporter: string | null;
  status: string;
  ping_count: number | null;
  hidden_tail_h: number | null;
  declared_h: number | null;
  why: string;
}

export interface TrackRoutePoint {
  t: string;
  lat: number;
  lon: number;
  speed: number | null;
  in_origin: boolean;
}

export interface TrackFence {
  key: string;
  name: string;
  role: string;
  lat: number;
  lon: number;
  radius_m: number;
  source: string | null;
}

export interface TripTrack {
  trip: {
    trip_no: number;
    origin: string;
    destination: string;
    transporter: string | null;
    asset_id: string | null;
    close_reason: string | null;
    trip_class: string | null;
    dt_booking: string | null;
    dt_trip_start: string | null;
    dt_trip_eta: string | null;
    dt_trip_ata: string | null;
    dt_geofence_out: string | null;
    geofence_out_gap_min: number | null;
    geofence_out_status: string | null;
    ping_count: number | null;
  };
  detention: {
    declared_h: number | null;
    hidden_tail_h: number | null;
    true_total_h: number | null;
    understated_by_pct: number | null;
    note: string;
  };
  fences: { origin: TrackFence | null; destination: TrackFence | null };
  route: TrackRoutePoint[];
  events: {
    fence_key: string; role: string; event: string;
    t: string; gap_min: number | null; lat: number; lon: number;
  }[];
  sampling: {
    pings_total: number;
    points_returned: number;
    in_origin_kept_whole: number;
    route_stride: number;
    note: string;
  };
}

export const getDemoTrips = (limit = 12) =>
  backendApi.get<DemoTrip[]>('/geofence/demo-trips', { params: { limit } });

export const getTripTrack = (tripNo: number) =>
  backendApi.get<TripTrack>(`/geofence/trip/${tripNo}/track`);

// ------------------------------------------------ client geofence handover

export interface PreviewFence {
  row: number;
  key: string;
  name: string;
  role: string;
  lat: number;
  lon: number;
  radius_m: number;
  action: 'new' | 'replaces';
  replaces_source: string | null;
  shift_km: number | null;
  radius_change_m: number | null;
  matches_trips: boolean;
  trips_affected: number;
  did_you_mean: string[];
}

export interface ImportPreview {
  columns_detected: Record<string, string | null>;
  unreadable_fields: string[];
  rows_read: number;
  accepted_count: number;
  rejected_count: number;
  accepted: PreviewFence[];
  rejected: { row: number; reason: string; data: Record<string, unknown> }[];
  new_count: number;
  replaces_count: number;
  unmatched: { name: string; did_you_mean: string[] }[];
  trips_affected: number;
  large_moves: PreviewFence[];
  note: string;
}

/** Validate a client geofence file. Writes nothing. */
export const previewClientFences = (fences: Record<string, unknown>[]) =>
  backendApi.post<ImportPreview>('/geofence/import/preview', fences);

export interface ImportResult {
  inserted: number;
  updated: number;
  columns_detected: Record<string, string | null>;
  rejected_count: number;
  rejected: { row: number; reason: string; data: Record<string, unknown> }[];
  unmatched_count: number;
  unmatched_node_names: string[];
  note: string;
}

/** Commit client geofences. `cutover` deactivates every derived fence. */
export const importClientFences = (
  fences: Record<string, unknown>[],
  cutover = false,
) =>
  backendApi.post<ImportResult>('/geofence/import', fences, { params: { cutover } });

/**
 * The trips behind one scorecard row, with the raw inputs each verdict came
 * from — ping count, first-ping lag, provider uptime, geofence status. This is
 * what makes the percentages checkable rather than asserted.
 */
export const getGpsDimensionDetail = (
  dimension: 'transporter' | 'region',
  value: string,
  tripClass: TripClass = 'zonal',
) =>
  backendApi.get<any>('/geofence/gps/detail', {
    params: { dimension, value, trip_class: tripClass },
  });

/**
 * The consignor's "customer not geo fenced" complaint, evaluated against one
 * trip as four separate checks.
 */
export const getTripDeliveryGeofence = (tripNo: number | string) =>
  backendApi.get<any>(`/geofence/trip/${tripNo}/delivery-geofence`);

/** Every trip to one destination, with where its trail actually stopped. */
export const getDestinationDetail = (destination: string, tripClass: TripClass = 'zonal') =>
  backendApi.get<any>('/geofence/gps/destination-detail', {
    params: { destination, trip_class: tripClass },
  });
