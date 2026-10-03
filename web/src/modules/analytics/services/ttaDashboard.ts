import { backendApi } from './api';

/** Shared global filters for every TTA analytics page. */
export interface TTAFilters {
  dateFrom: string;
  dateTo: string;
  transporters: string[];
  destinations: string[];
  vehicleCategories: string[];
  ownMarket: string[];
  consignors: string[];
}

export const EMPTY_FILTERS: TTAFilters = {
  dateFrom: '', dateTo: '', transporters: [], destinations: [],
  vehicleCategories: [], ownMarket: [], consignors: [],
};

/** Multi-values travel as ||-separated strings (names may contain commas). */
export function filtersToParams(f: TTAFilters): Record<string, string> {
  const p: Record<string, string> = {};
  if (f.dateFrom) p.date_from = f.dateFrom;
  if (f.dateTo) p.date_to = f.dateTo;
  if (f.transporters.length) p.transporters = f.transporters.join('||');
  if (f.destinations.length) p.destinations = f.destinations.join('||');
  if (f.vehicleCategories.length) p.vehicle_categories = f.vehicleCategories.join('||');
  if (f.ownMarket.length) p.own_market = f.ownMarket.join('||');
  if (f.consignors.length) p.consignors = f.consignors.join('||');
  return p;
}

export interface DashboardMeta {
  rows: number;
  date_min: string | null;
  date_max: string | null;
  transporters: string[];
  destinations: string[];
  vehicle_categories: string[];
  consignors: string[];
  own_market: string[];
}

export interface KPIBlock {
  trips?: number; transporters?: number; vehicles?: number; destinations?: number;
  total_km?: number; avg_km_per_trip?: number; otd_pct?: number;
  avg_transit_hours?: number; median_transit_hours?: number;
  avg_delay_when_late_hours?: number; avg_detention_hours?: number;
  avg_plant_vivo_hours?: number; avg_dispatch_lead_hours?: number;
  speed_violations?: number; avg_violations_per_trip?: number;
  avg_gps_uptime?: number; avg_speed_kmph?: number; market_share_pct?: number;
}

export interface GroupRow {
  name: string; trips: number; otd_pct: number | null;
  avg_transit_hours: number | null; median_transit_hours: number | null;
  avg_planned_transit_hours: number | null; avg_detention_hours: number | null;
  avg_distance_km: number | null; total_km: number | null;
  avg_speed_kmph: number | null; speed_violations: number;
  avg_gps_uptime: number | null; vehicles: number; destinations: number;
  violations_per_trip: number | null; share_pct: number | null;
  schedule_variance_hours: number | null;
  /** Trips the on-time rate is actually over, the late ones, and the Wilson 95% interval. */
  judged_trips: number; late_trips: number;
  otd_ci_low: number | null; otd_ci_high: number | null;
  /** Set when grouping by destination (the city): its state, from the consignee PIN. */
  state?: string | null;
}

export interface BoxGroup {
  group: string; count: number; min: number; max: number;
  q1: number; median: number; q3: number;
  whisker_lo: number; whisker_hi: number; mean: number; outliers: number[];
}

type P = Record<string, string>;

export const getDashMeta = () => backendApi.get<DashboardMeta>('/tta/dashboard/meta');
export const getDashKpis = (p: P) =>
  backendApi.get<{ current: KPIBlock; previous: KPIBlock; delta_pct: Record<string, number> }>(
    '/tta/dashboard/kpis', { params: p });
export const getDashTimeseries = (granularity: 'D' | 'W' | 'M', p: P) =>
  backendApi.get<{ granularity: string; series: any[] }>('/tta/dashboard/timeseries',
    { params: { granularity, ...p } });
export const getDashGroup = (by: string, minTrips: number, p: P) =>
  backendApi.get<{ by: string; rows: GroupRow[] }>('/tta/dashboard/group',
    { params: { by, min_trips: String(minTrips), ...p } });
export const getDashFunnel = (p: P) =>
  backendApi.get<{ stages: { stage: string; count: number }[] }>('/tta/dashboard/funnel', { params: p });
export const getDashGeo = (p: P) =>
  backendApi.get<any>('/tta/dashboard/geo', { params: p });
export interface StateRow {
  state: string;
  lat: number | null;
  lon: number | null;
  trips: number;
  share_pct: number | null;
  otd_pct: number | null;
  otd_ci_low: number | null;
  otd_ci_high: number | null;
  judged_trips: number;
  late_trips: number | null;
  avg_transit_hours: number | null;
  median_transit_hours: number | null;
  avg_detention_hours: number | null;
  avg_distance_km: number | null;
  total_km: number | null;
  avg_speed_kmph: number | null;
  speed_violations: number;
  violations_per_trip: number | null;
  avg_gps_uptime: number | null;
  destinations: number;
  transporters: number;
  vehicles: number;
  consignees: number;
}

export interface GeoStates {
  origin: { name: string; lat: number; lon: number };
  states: StateRow[];
  unmapped: { destination: string; trips: number }[];
  mapped_pct: number;
  totals: {
    states?: number; trips?: number; mapped_trips?: number; otd_pct?: number | null;
    top_state?: string | null; top3_share_pct?: number | null;
  };
}

export const getDashGeoStates = (p: P) =>
  backendApi.get<GeoStates>('/tta/dashboard/geo/states', { params: p });

export const getDashDowHour = (p: P) =>
  backendApi.get<{ rows: string[]; cols: number[]; values: number[][] }>(
    '/tta/dashboard/heatmap/dow-hour', { params: p });
export const getDashPivot = (rows: string, metric: string, top: number, p: P, cols = 'dept_month') =>
  backendApi.get<{ rows: string[]; cols: string[]; values: (number | null)[][] }>(
    '/tta/dashboard/heatmap/pivot', { params: { rows, cols, metric, top: String(top), ...p } });
export const getDashCorrelation = (p: P) =>
  backendApi.get<{ labels: string[]; values: (number | null)[][] }>(
    '/tta/dashboard/correlation', { params: p });
export const getDashDistribution = (metric: string, p: P) =>
  backendApi.get<any>('/tta/dashboard/distribution', { params: { metric, ...p } });
export const getDashBoxplot = (groupBy: string, metric: string, top: number, p: P) =>
  backendApi.get<{ groups: BoxGroup[] }>('/tta/dashboard/boxplot',
    { params: { group_by: groupBy, metric, top: String(top), ...p } });
export const getDashFleet = (p: P) => backendApi.get<any>('/tta/dashboard/fleet', { params: p });
export const getDashOutliers = (z: number, p: P) =>
  backendApi.get<{ z_threshold: number; rows: any[] }>('/tta/dashboard/outliers',
    { params: { z: String(z), ...p } });
export const getDashRecords = (limit: number, p: P) =>
  backendApi.get<{ total: number; returned: number; rows: any[] }>(
    '/tta/dashboard/records', { params: { limit: String(limit), ...p } });
