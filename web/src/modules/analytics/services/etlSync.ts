import { backendApi } from './api';

/** The two upstream trip feeds. Both write the same tables, tagged apart by
 *  tta_trips.s_trip_class — see backend/app/services/tta_lanes.py. */
export type LaneKey = 'zonal' | 'local';

/** Pre-lanes source ids, still returned by the API as `trip_source` on each
 *  lane. Kept so anything holding one of these values keeps resolving. */
export type TripSource = 'tta_report' | 'local_report';

export const TRIP_SOURCE_LABELS: Record<TripSource, string> = {
  tta_report: 'TTA Report (per consignor)',
  local_report: 'TTA Local Report (all consignors)',
};

/** Legacy source id -> lane key. */
export const LANE_FOR_SOURCE: Record<TripSource, LaneKey> = {
  tta_report: 'zonal',
  local_report: 'local',
};

export const LANE_KEYS: LaneKey[] = ['zonal', 'local'];

export interface SyncSettings {
  lane?: LaneKey;
  label?: string;
  enabled: boolean;
  interval_minutes: number;
  lookback_minutes: number;
  entity_ids: number[];
  needs_entities?: boolean;
  ready?: boolean;
  next_scheduled_run?: string | null;
}

export interface LastRun {
  status?: string;
  trigger?: string;
  lane?: LaneKey;
  window_start?: string;
  window_end?: string;
  blocks_fetched?: number;
  trips_upserted?: number;
  gps_inserted?: number;
  gps_skipped?: number;
  gps_failed?: number;
  /** Set when the run could not reach back far enough: that span is
   *  missing from the database until a backfill covers it. Ephemeral — the
   *  next clean tick overwrites it. The durable record is `SyncStatus.data_gaps`. */
  data_gap?: { id?: number; from: string; to: string; hours: number; state?: GapState } | null;
  error?: string;
  finished_at?: string;
  elapsed_seconds?: number;
}

/** A span the ETL knows it never fetched, held in tta_data_gaps.
 *
 *  `open` and an expired `snoozed` show in the banner. `dismissed` does not —
 *  but it still counts on the lane card, because declining to backfill does not
 *  put the data back. Only `recovered` means the span is actually filled. */
export type GapState = 'open' | 'snoozed' | 'dismissed' | 'recovered';

export interface DataGapRow {
  id: number;
  lane: LaneKey;
  gap_start: string;
  gap_end: string;
  hours: number;
  state: GapState;
  detected_at: string;
  detected_by?: string | null;
  decided_at?: string | null;
  snooze_until?: string | null;
  note?: string | null;
  recovered_at?: string | null;
}

export interface GapSummary {
  open: number;
  snoozed: number;
  dismissed: number;
  recovered: number;
  /** open + snoozed + dismissed — everything still missing. */
  unresolved: number;
  missing_hours: number;
  earliest_missing: string | null;
}

/** What the next run of a lane will fetch, and what it cannot reach. */
export interface WindowPlan {
  lane: LaneKey;
  should_run: boolean;
  reason: string;
  watermark: string | null;
  lag_minutes: number | null;
  window_start: string;
  window_end: string;
  /** Hours older than the window cap: never fetched automatically, filed as a gap. */
  unreachable_hours: number;
}

export interface BackfillState {
  status: string;
  lane?: LaneKey;
  total_chunks?: number;
  chunks_done?: number;
  percent?: number;
  trips_upserted?: number;
  gps_inserted?: number;
  gps_skipped?: number;
  from_date?: string;
  to_date?: string;
  errors?: string[];
  error?: string;
  elapsed_seconds?: number;
}

/** Live state of ONE lane. */
export interface LaneStatus {
  lane: LaneKey;
  trip_source?: TripSource;
  label: string;
  description: string;
  endpoint: string;
  running: boolean;
  enabled: boolean;
  ready: boolean;
  interval_minutes: number;
  lookback_minutes: number;
  entity_ids: number[];
  needs_entities: boolean;
  watermark: string | null;
  /** Same value as `watermark`, named for the question the screen answers:
   *  "until when do I have data?". Read it WITH `gaps` — the watermark is the
   *  sync frontier, and a lane can be current at the tail and still be missing
   *  a fortnight behind it. */
  data_through?: string | null;
  /** Counts from the durable register. Absent on an older backend. */
  gaps?: GapSummary;
  /** Minutes between the source clock and the last fully-captured window. */
  /** Absent on a backend older than the lag metric — treat as unknown,
   *  never as unhealthy. */
  lag_minutes?: number | null;
  lag_limit_minutes?: number;
  expected_source_lag_minutes?: number;
  healthy?: boolean;
  next_scheduled_run: string | null;
  last_run: LastRun;
}

/** /status returns every lane under `lanes`, plus shared auth + backfill. */
export interface SyncStatus extends LaneStatus {
  lanes: Record<LaneKey, LaneStatus>;
  any_running: boolean;
  auth: { authenticated: boolean; has_refresh_token: boolean; access_expires_at: string | null };
  backfill: BackfillState;
  /** Open gaps plus snoozed ones whose time is up — the banner's input.
   *  Served on /status so the screen keeps a single poll. */
  data_gaps?: DataGapRow[];
}

export interface LaneInfo {
  key: LaneKey;
  label: string;
  description: string;
  endpoint: string;
  needs_entities: boolean;
  ready: boolean;
  enabled: boolean;
  interval_minutes: number;
  lookback_minutes: number;
  next_scheduled_run: string | null;
}

/** Observed from the running APScheduler — proof the worker is alive, rather
 *  than config read back to itself. */
export interface SchedulerJob {
  id: string;
  name: string;
  trigger: string;
  next_run_time: string | null;
  max_instances: number;
  coalesce: boolean;
}

export interface SchedulerState {
  alive: boolean;
  jobs: SchedulerJob[];
  server_time: string;
  lane_jobs: Record<LaneKey, string>;
}

export interface SyncRun {
  id: number;
  trigger_type: string;
  lane: LaneKey;
  status: string;
  window_start: string | null;
  window_end: string | null;
  blocks_fetched: number;
  trips_upserted: number;   // records into tta_trips
  gps_inserted: number;     // records into tta_trip_gps
  gps_skipped: number;
  gps_failed: number;       // trips whose GPS was deferred for retry
  error: string | null;
  started_at: string | null;
  finished_at: string | null;
  elapsed_seconds: number | null;
  created_at: string | null;
}

export interface EtlLogEvent {
  ts: string;
  kind: string;               // auth | trips | gps | run
  method?: string | null;
  url?: string | null;
  payload?: any;
  params?: any;
  status?: number | string | null;
  elapsed_ms?: number | null;
  error?: string | null;
  detail?: string;            // "<lane>:<trigger>" on run events
  lane?: LaneKey;
  [k: string]: any;
}

export interface EtlLog {
  date: string;
  file: string;
  total: number;
  events: EtlLogEvent[];
}

export const getSyncStatus = () => backendApi.get<SyncStatus>('/tta/sync/status');
export const getLanes = () => backendApi.get<{ lanes: LaneInfo[] }>('/tta/sync/lanes');
export const getSchedulerState = () => backendApi.get<SchedulerState>('/tta/sync/scheduler');

/** `lane` empty = every lane's runs interleaved. */
export const getSyncRuns = (limit = 10, lane: LaneKey | '' = '') =>
  backendApi.get<{ runs: SyncRun[] }>('/tta/sync/runs', { params: { limit, lane } });

export const getSyncLogs = (limit = 200, kind = '', errors_only = false) =>
  backendApi.get<EtlLog>('/tta/sync/logs', { params: { limit, kind, errors_only } });

export const getSyncSettings = (lane: LaneKey) =>
  backendApi.get<SyncSettings>('/tta/sync/settings', { params: { lane } });

export const updateSyncSettings = (lane: LaneKey, body: Partial<SyncSettings>) =>
  backendApi.put('/tta/sync/settings', body, { params: { lane } });

export const runSyncNow = (lane: LaneKey) =>
  backendApi.post('/tta/sync/run', null, { params: { lane } });

export const startBackfill = (lane: LaneKey, from_date: string, to_date: string, chunk_days: number) =>
  backendApi.post('/tta/sync/backfill', { from_date, to_date, chunk_days }, { params: { lane } });

export const getBackfillStatus = () => backendApi.get<BackfillState>('/tta/sync/backfill/status');

/** `lane`/`state` empty = no filter. `state` is comma-separated. */
export const getDataGaps = (lane: LaneKey | '' = '', state = '') =>
  backendApi.get<{ gaps: DataGapRow[]; summary: Record<LaneKey, GapSummary> }>(
    '/tta/sync/gaps', { params: { lane, state } });

/** Decline this span. Leaves the banner, keeps counting on the lane card. */
export const dismissGap = (id: number, note?: string) =>
  backendApi.post<{ gap: DataGapRow }>(`/tta/sync/gaps/${id}/dismiss`, { note: note ?? null });

/** Hide it for `hours`, then show it again. */
export const snoozeGap = (id: number, hours = 24) =>
  backendApi.post<{ gap: DataGapRow }>(`/tta/sync/gaps/${id}/snooze`, { snooze_hours: hours });

/** Undo a dismiss or snooze. */
export const reopenGap = (id: number) =>
  backendApi.post<{ gap: DataGapRow }>(`/tta/sync/gaps/${id}/reopen`, {});

/** From when to when the next run of each lane will fetch. Read-only. */
export const getWindowPreview = () =>
  backendApi.get<{ max_window_hours: number; lanes: Record<LaneKey, WindowPlan> }>(
    '/tta/sync/window-preview');
