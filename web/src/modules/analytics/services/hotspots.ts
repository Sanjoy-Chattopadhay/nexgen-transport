import { backendApi } from './api';

/**
 * Pilferage hotspot discovery.
 *
 * Consignor scoping is applied transparently by the backendApi interceptor —
 * these calls never pass a consignor id themselves.
 */

export interface HotspotComponents {
  carriers: number;
  vehicles: number;
  isolation: number;
  dwell: number;
  night: number;
  night_share_adj: number;
}

export interface HotspotLabel {
  status: string;
  note: string | null;
}

export interface Hotspot {
  id: number;
  s_place_key: string;
  d_lat: number;
  d_long: number;
  i_radius_m: number | null;
  i_stops: number;
  i_trips: number;
  i_vehicles: number;
  i_carriers: number;
  d_median_dwell_min: number | null;
  d_night_share: number | null;
  d_night_share_adj: number | null;
  i_median_isolation_m: number | null;
  s_nearest_node: string | null;
  i_nearest_node_m: number | null;
  d_score: number;
  s_components: HotspotComponents | null;
  b_low_support: number;
  s_amenity_hint: string | null;
  dt_first_seen: string | null;
  dt_last_seen: string | null;
  label: HotspotLabel | null;
}

export interface HotspotMember {
  id: number;
  i_trip_no: number;
  s_asset_id: string | null;
  s_trans_name: string | null;
  dt_start: string;
  dt_end: string;
  d_duration_min: number;
  i_hour: number;
  d_lat: number;
  d_long: number;
  i_wpnt_mt: number | null;
  s_wpnt: string | null;
  s_driver_name: string | null;
  route: string;
}

/** One stop row from the corpus, with the cluster context when it has one. */
export interface HotspotStopRecord extends HotspotMember {
  s_place_class: string;
  cluster_id: number | null;
  d_score: number | null;
  s_nearest_node: string | null;
  night: boolean;
}

/** A vehicle's (or carrier's) behaviour at ONE place.
 *
 *  Derived on the server from the very stop rows the evidence table lists, so
 *  "16 vehicles" and the 16 rows under it are the same fact. */
export interface HotspotRollup {
  s_asset_id?: string;
  s_trans_name?: string;
  stops: number;
  trips: number;
  vehicles: number;
  carriers: number;
  night_stops: number;
  night_share: number;
  total_dwell_min: number;
  median_dwell_min: number | null;
  longest_dwell_min: number;
  first_seen: string | null;
  last_seen: string | null;
}

export interface HotspotListResponse {
  items: Hotspot[];
  kpis: {
    clusters: number | null;
    low_support: number | null;
    amenity_adjacent: number | null;
    stops: number | null;
    top_score: number | null;
  };
  disclaimer: string;
  consignor: string | null;
}

export interface HotspotDetailResponse {
  cluster: Hotspot & { cnr_id: number };
  members: HotspotMember[];
  hour_profile: number[];
  carrier_mix: { carrier: string | null; stops: number; vehicles: number }[];
  /** Per-vehicle behaviour at this place, worst total dwell first. */
  vehicle_mix: HotspotRollup[];
  /** Same, grouped by carrier. */
  carrier_rollup: HotspotRollup[];
  disclaimer: string;
}

export interface HotspotStatus {
  corpus: {
    stops: number | null;
    trips: number | null;
    vehicles: number | null;
    carriers: number | null;
    avg_duration_min: number | null;
    isolated_stops: number | null;
    night_stops: number | null;
    candidate_stops: number | null;
    first_stop: string | null;
    last_stop: string | null;
  };
  trips_extracted: number;
  trips_without_stops: number;
  trips_pending: number;
  clusters: { clusters: number; last_built: string | null };
}

export const getHotspots = (limit = 50, includeLowSupport = false, includeAmenities = false) =>
  backendApi.get<HotspotListResponse>('/hotspots', {
    params: {
      limit,
      include_low_support: includeLowSupport,
      include_amenities: includeAmenities,
    },
  });

export const getHotspotDetail = (id: number) =>
  backendApi.get<HotspotDetailResponse>(`/hotspots/${id}`);

export const getHotspotStatus = () => backendApi.get<HotspotStatus>('/hotspots/status');

/** The individual stops behind a headline count.
 *  `clustered=false` returns the whole extracted corpus, masked stops included. */
export const getHotspotStops = (clustered = true, limit = 500) =>
  backendApi.get<{
    stops: HotspotStopRecord[];
    count: number;
    truncated: boolean;
    clustered: boolean;
  }>('/hotspots/stops', { params: { clustered, limit } });

/** Minutes-long batch rebuild — needs a generous timeout. */
export const refreshHotspots = (runMasking = true) =>
  backendApi.post<any>('/hotspots/refresh', null, {
    params: { run_masking: runMasking },
    timeout: 1_800_000,
  });

export const extractStopEvents = (limit = 500) =>
  backendApi.post<any>('/hotspots/extract', null, { params: { limit }, timeout: 600_000 });

export const labelHotspot = (id: number, status: string, note: string, labelledBy: string) =>
  backendApi.post<any>(`/hotspots/${id}/label`, { status, note, labelled_by: labelledBy });
