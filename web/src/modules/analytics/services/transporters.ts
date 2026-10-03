// Carrier data for the Transporters tab of Compare & Benchmark. The
// standalone /transporters section was removed, so there is no per-carrier
// profile or league-table call here any more.
import { backendApi } from './api';

/** Shared date window for the transporter section (same params as /analytics). */
export interface TransporterFilters {
  date_from?: string;
  date_to?: string;
}

export interface TransporterRow {
  name: string;
  trips: number;
  otd_pct: number | null;
  avg_transit_hours: number | null;
  median_transit_hours: number | null;
  avg_planned_transit_hours: number | null;
  schedule_variance_hours: number | null;
  avg_detention_hours: number | null;
  avg_plant_vivo_hours: number | null;
  avg_dispatch_lead_hours: number | null;
  avg_distance_km: number | null;
  total_km: number | null;
  avg_speed_kmph: number | null;
  speed_violations: number;
  violations_per_trip: number | null;
  avg_gps_uptime: number | null;
  avg_delay_when_late_hours: number | null;
  market_pct: number | null;
  vehicles: number;
  drivers: number;
  destinations: number;
  consignors: number;
  trips_per_vehicle: number | null;
  share_pct: number | null;
  active_days: number;
  first_trip: string | null;
  last_trip: string | null;
  score: number | null;
  grade: string;
  rank: number | null;
}

export interface FleetBenchmark {
  transporters: number;
  qualified: number;
  trips: number;
  otd_pct: number | null;
  median_otd_pct: number | null;
  median_transit_hours: number | null;
  median_schedule_variance_hours: number | null;
  median_detention_hours: number | null;
  median_dispatch_lead_hours: number | null;
  median_violations_per_trip: number | null;
  median_gps_uptime: number | null;
  median_speed_kmph: number | null;
  total_km: number | null;
  top3_share_pct: number | null;
  hhi: number | null;
}

export interface Insight { tone: 'good' | 'bad' | 'info'; text: string }

export interface MixRow { name: string; trips: number; otd_pct: number | null }

export interface LaneRow {
  lane: string; origin: string; destination: string; trips: number;
  otd_pct: number | null; avg_transit_hours: number | null;
  avg_planned_transit_hours: number | null; schedule_variance_hours: number | null;
  avg_distance_km: number | null; avg_detention_hours: number | null;
}

export interface SpreadStats {
  count: number; mean: number | null; median: number | null; std: number | null;
  cv_pct: number | null; q1: number | null; q3: number | null; iqr: number | null;
  whisker_lo: number | null; whisker_hi: number | null;
  min: number | null; max: number | null; p90: number | null;
}

export interface Spread {
  stats: Partial<SpreadStats>;
  histogram: { bin_start: number; bin_end: number; mid: number; count: number }[];
}

/** Identity card — counts and dates only, no judgement. */
export interface TransporterBio {
  name: string;
  trips: number;
  share_pct: number | null;
  first_trip: string | null;
  last_trip: string | null;
  window_days: number;
  active_days: number;
  activity_pct: number | null;
  trips_per_active_day: number | null;
  vehicles: number;
  drivers: number;
  destinations: number;
  lanes: number;
  states: number;
  consignors: number;
  consignees: number;
  total_km: number | null;
  own_pct: number | null;
  market_pct: number | null;
  top_make: string | null;
  makes: number;
  top_device: string | null;
  vehicle_categories: string[];
}

/** This carrier's row from the geofence module's GPS coverage scorecard. */
export interface TransporterGpsQuality {
  available: boolean;
  reason?: string;
  trips?: number;
  gps_ok_pct?: number | null;
  gps_ok_ci_low?: number | null;
  gps_ok_ci_high?: number | null;
  gps_ok_ci_width?: number | null;
  silent_trips?: number;
  silent_pct?: number | null;
  died_at_origin?: number;
  died_at_origin_pct?: number | null;
  late_start?: number;
  gappy?: number;
  gps_on_time_pct?: number | null;
  median_ping_count?: number;
  rank?: number | null;
  carriers_ranked?: number;
  fleet_median_ok_pct?: number | null;
}

export interface StateRow {
  state: string;
  trips: number;
  otd_pct: number | null;
  avg_transit_hours: number | null;
  total_km: number | null;
}

export interface VehicleRow {
  vehicle_no: string;
  vehicle_type: string | null;
  vehicle_category: string | null;
  own_market: string | null;
  trips: number;
  otd_pct: number | null;
  total_km: number | null;
  avg_transit_hours: number | null;
  avg_speed_kmph: number | null;
  speed_violations: number;
  violations_per_trip: number | null;
  avg_gps_uptime: number | null;
  last_trip: string | null;
}

export interface DriverRow {
  name: string;
  trips: number;
  otd_pct: number | null;
  avg_transit_hours: number | null;
  avg_speed_kmph: number | null;
  speed_violations: number;
  violations_per_trip: number | null;
  total_km: number | null;
  vehicles: number;
  destinations: number;
  last_trip: string | null;
}

export interface TransporterDetail {
  transporter: string;
  bio: TransporterBio;
  grade: CarrierGrade;
  fleet_shape: FleetShape;
  origin_detention: OriginDetention;
  kpis: TransporterRow;
  fleet: FleetBenchmark;
  gps_quality: TransporterGpsQuality;
  states: StateRow[];
  insights: Insight[];
  monthly: { month: string; trips: number; otd_pct: number | null; avg_transit_hours: number | null; avg_detention_hours: number | null; total_km: number | null; speed_violations: number }[];
  lanes: LaneRow[];
  vehicles: VehicleRow[];
  drivers: DriverRow[];
  delivery: MixRow[];
  own_market: MixRow[];
  vehicle_category: MixRow[];
  consignor_mix: MixRow[];
  device_type: MixRow[];
  transit_spread: Spread;
  detention_spread: Spread;
  rhythm: { rows: string[]; cols: number[]; values: number[][] };
  risk_trips: any[];
  recent_trips: any[];
}

/** One point per period, plus one numeric column per carrier name. */
export type PeriodPoint = { period: string } & Record<string, number | string | null>;

export interface StackedMix {
  categories: string[];
  rows: ({ name: string } & Record<string, number | string>)[];
}

export interface BoxGroup {
  group: string; count: number; min: number; max: number;
  q1: number; median: number; q3: number;
  whisker_lo: number; whisker_hi: number; mean: number; outliers: number[];
}

export interface MoverRow {
  name: string; trips: number; prev_trips: number | null;
  otd_pct: number | null; prev_otd_pct: number | null;
  otd_delta: number | null; trips_delta: number | null;
}

export interface LaneDependencyRow {
  lane: string; trips: number; carriers: number;
  otd_pct: number | null; avg_transit_hours: number | null;
  top_carrier: string; top_share_pct: number | null;
}

export interface TransporterAnalytics {
  granularity: string;
  top_names: string[];
  trend_trips: PeriodPoint[];
  trend_otd: PeriodPoint[];
  trend_transit: PeriodPoint[];
  heatmap_otd: { rows: string[]; cols: string[]; values: (number | null)[][] };
  boxplot_transit: BoxGroup[];
  boxplot_detention: BoxGroup[];
  mix_own_market: StackedMix;
  mix_category: StackedMix;
  mix_delivery: StackedMix;
  movers: MoverRow[];
  lane_dependency: LaneDependencyRow[];
}

export const listTransporters = (f: TransporterFilters = {}, minTrips?: number, search = '') =>
  backendApi.get<{ data: TransporterRow[]; total: number; fleet: FleetBenchmark; min_trips: number }>(
    '/transporters', { params: { ...f, search, min_trips: minTrips } });

// ---------------------------------------------------------------------------
// Head-to-head carrier comparison (Compare & benchmark → Transporters tab)
// ---------------------------------------------------------------------------

/** One carrier's numbers on one shared lane. */
export interface LaneCell {
  trips: number;
  otd_pct: number | null;
  avg_transit_hours: number | null;
  avg_detention_hours: number | null;
}

export interface SharedLaneRow {
  lane: string;
  /** how many of the picked carriers run this lane (always ≥ 2) */
  carriers: number;
  trips: number;
  avg_distance_km: number | null;
  /** positional against `names` — null where that carrier never ran the lane */
  cells: (LaneCell | null)[];
}

export interface CarrierCompare {
  name: string;
  trips: number;
  /** empty object when the carrier has no trips in the window */
  kpis: Partial<TransporterRow>;
  insights: Insight[];
  monthly: TransporterDetail['monthly'];
  lanes: LaneRow[];
  transit_spread: Spread;
  detention_spread: Spread;
  delivery: MixRow[];
  own_market: MixRow[];
  vehicle_category: MixRow[];
}

export interface TransporterComparison {
  names: string[];
  /** picked carriers with zero trips in the window — surfaced, not hidden */
  missing: string[];
  granularity: string;
  min_trips: number;
  fleet: FleetBenchmark;
  carriers: CarrierCompare[];
  box_transit: BoxGroup[];
  box_detention: BoxGroup[];
  trend_trips: PeriodPoint[];
  trend_otd: PeriodPoint[];
  trend_transit: PeriodPoint[];
  shared_lanes: SharedLaneRow[];
}

/** Carrier names are ||-joined, not comma-joined — they contain commas. */
export const compareTransporters = (
  names: string[], f: TransporterFilters = {}, minTrips?: number, granularity = 'M',
) =>
  backendApi.get<TransporterComparison>('/transporters/compare', {
    params: { ...f, names: names.join('||'), min_trips: minTrips, granularity },
    timeout: 120000,
  });


// ---------------------------------------------------------------------------
// One carrier's whole record
// ---------------------------------------------------------------------------

/** Carrier names contain slashes and dots, so the path segment is encoded. */
export const getTransporter = (name: string, f: TransporterFilters = {}, minTrips?: number) =>
  backendApi.get<TransporterDetail>(`/transporters/${encodeURIComponent(name)}`,
    { params: { ...f, min_trips: minTrips }, timeout: 120000 });

// ---------------------------------------------------------------------------
// Reliability matrix — volume against reliability
// ---------------------------------------------------------------------------

export type Quadrant = 'core' | 'critical' | 'grow' | 'review';

export interface MatrixCarrier {
  name: string;
  trips: number;
  share_pct: number | null;
  otd_pct: number | null;
  /** Wilson bounds: how far the on-time rate could be from its point estimate */
  otd_ci_low: number | null;
  otd_ci_high: number | null;
  otd_judged_trips: number;
  reliability_score: number | null;
  transit_cv_pct: number | null;
  schedule_variance_hours: number | null;
  avg_transit_hours: number | null;
  avg_detention_hours: number | null;
  violations_per_trip: number | null;
  avg_gps_uptime: number | null;
  total_km: number | null;
  vehicles: number;
  destinations: number;
  market_pct: number | null;
  score: number | null;
  grade: string;
  quadrant: Quadrant;
  quadrant_label: string;
  qualified: boolean;
}

export interface QuadrantSummary {
  label: string;
  action: string;
  carriers: number;
  trips: number;
  share_pct: number | null;
  median_otd_pct: number | null;
}

export interface ReliabilityMatrix {
  carriers: MatrixCarrier[];
  axes: {
    volume_split: number;
    volume_split_label: string;
    otd_target: number;
    max_trips: number;
  };
  quadrants: Record<Quadrant, QuadrantSummary>;
  min_trips: number;
  fleet: FleetBenchmark;
}

export const getReliabilityMatrix = (
  f: TransporterFilters = {}, minTrips?: number, otdTarget?: number,
) =>
  backendApi.get<ReliabilityMatrix>('/transporters/reliability-matrix',
    { params: { ...f, min_trips: minTrips, otd_target: otdTarget }, timeout: 120000 });

// ---------------------------------------------------------------------------
// Grading, fleet shape, origin detention, best-by-lane
// ---------------------------------------------------------------------------

/** One graded axis, with the published bands it was marked against. */
export interface GradeComponent {
  key: string;
  label: string;
  weight: number;
  value: number | null;
  unit: string;
  points: number | null;
  why: string;
  bands: { at: number; points: number }[];
  lower_is_better: boolean;
}

export interface CarrierGrade {
  grade: string;
  grade_score: number | null;
  rated: boolean;
  meaning?: string;
  reason?: string | null;
  components: GradeComponent[];
  measured_weight: number;
  bands: { at: number; grade: string; meaning: string }[];
}

/** What the fleet actually looks like — not its average. */
export interface FleetShape {
  vehicles: number;
  trips?: number;
  median_trips_per_vehicle?: number;
  max_trips_per_vehicle?: number;
  single_trip_vehicles?: number;
  single_trip_pct?: number | null;
  top10_share_pct?: number | null;
  repeat_vehicles?: number;
}

export interface DetentionWindow {
  trips: number;
  mean_hours: number | null;
  median_hours: number | null;
  p90_hours: number | null;
  fleet_median_hours: number | null;
}

export interface OriginDetention {
  declared: DetentionWindow;
  fence_tail: DetentionWindow;
  true_total: DetentionWindow;
  declared_understates_by_pct: number | null;
  measured_trips: number;
  total_trips: number;
}

export interface LaneCarrier {
  transporter: string;
  trips: number;
  otd_pct: number | null;
  judged: number;
  otd_ci_low: number | null;
  otd_ci_high: number | null;
  avg_transit_hours: number | null;
  avg_detention_hours: number | null;
  avg_distance_km: number | null;
  schedule_variance_hours: number | null;
}

export interface LaneChoice {
  lane: string;
  carriers: number;
  trips: number;
  avg_distance_km: number | null;
  best: LaneCarrier;
  runner_up: LaneCarrier | null;
  worst: LaneCarrier;
  otd_gap_pts: number | null;
  all: LaneCarrier[];
}

export interface BestByLane {
  lanes: LaneChoice[];
  total_lanes: number;
  min_trips: number;
  /** Commodity-level ranking was asked for; this says why it cannot be built. */
  commodity: { available: boolean; reason: string; needed: string };
}

export const getBestByLane = (f: TransporterFilters = {}, minTrips?: number) =>
  backendApi.get<BestByLane>('/transporters/best-by-lane',
    { params: { ...f, min_trips: minTrips }, timeout: 120000 });
