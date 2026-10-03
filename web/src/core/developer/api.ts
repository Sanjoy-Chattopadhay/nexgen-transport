/**
 * The developer API (/api/v1/dev/*). It is answered by the supervisor itself,
 * not by a service, so this page keeps working with every service stopped --
 * which is when it is needed most.
 */

export interface ConsumerInfo {
  name: string; types: string[]; offset: number; processed: number; failed: number;
  last_error: string | null; last_error_at: string | null; waiting_gap: number | null;
  running: boolean; backoff_s: number;
}

export interface JobInfo {
  name: string; description: string; schedule: string; enabled: boolean; running: boolean;
  next_run: string | null; last_status: string | null; last_seconds: number | null;
  last_started: string | null; last_error: string | null; runs: number;
}

export interface RoleInfo {
  name: string; kind: 'api' | 'worker'; description: string; running: boolean;
  last_error: string | null; started_at: string | null;
  consumers?: ConsumerInfo[]; jobs?: JobInfo[]; loops?: string[];
}

export type ServiceState = 'stopped' | 'starting' | 'running' | 'stopping' | 'crashed';

export interface ServiceInfo {
  name: string; title: string; description: string; port: number;
  desired: 'running' | 'stopped'; state: ServiceState; pid: number | null;
  started_at: string | null; uptime_s: number | null; restarts_recent: number;
  last_exit_code: number | null; last_exit_at: string | null;
  health_status: string | null; health_error: string | null; health_at: string | null;
  roles: Record<string, RoleInfo>; routes: string[];
  declared_roles: { name: string; autostart: boolean; description: string }[];
  memory_mb?: number; cpu_percent?: number; processes?: number;
}

export interface Overview {
  product: string;
  gateway: { port: number; pid: number; started_at: string };
  database: { reachable: boolean; version?: string; error?: string };
  auth: string;
  services: ServiceInfo[];
  generated_at: string;
}

export interface JobRun {
  i_run_id: number; s_service: string; s_job: string;
  /** schedule | manual | event | boot */
  s_trigger: string;
  /** running | ok | failed | skipped */
  s_status: string;
  dt_started: string; dt_finished: string | null; d_seconds: number | null;
  s_error: string | null; j_summary: string | null;
}

export interface ConsumerRow {
  s_consumer: string; i_last_event_id: number; i_processed: number; i_failed: number;
  s_last_error: string | null; dt_last_error: string | null; dt_updated: string; head: number; lag: number;
}

export interface EventRow {
  i_event_id: number; s_type: string; i_tenant_id: number; s_source: string;
  j_payload: string; dt_created?: string;
}

export interface TableInfo {
  name: string; kind: 'table' | 'view'; rows?: number; data_mb?: number; index_mb?: number; partitioned?: boolean;
}

export interface SchemaInfo { database: string; key: string; tables: TableInfo[]; bytes: number; views: number }

export interface MigrationStatus {
  schema: string; database: string; applied: number; pending: number; pending_files: string[];
  changed_after_apply: string[]; repeatable: string[]; latest: string | null; error?: string;
}

export class DevError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function call<T>(method: 'GET' | 'POST', path: string): Promise<T> {
  const res = await fetch(`/api/v1/dev${path}`, { method });
  let body: any = null;
  try { body = await res.json(); } catch { /* not JSON */ }
  if (!res.ok) {
    const detail = body?.detail ?? body?.message ?? res.statusText;
    throw new DevError(res.status, typeof detail === 'string' ? detail : JSON.stringify(detail));
  }
  return body as T;
}

const q = (p: Record<string, string | number | boolean | undefined>) => {
  const u = new URLSearchParams();
  Object.entries(p).forEach(([k, v]) => { if (v !== undefined && v !== '') u.set(k, String(v)); });
  const s = u.toString();
  return s ? `?${s}` : '';
};

export const dev = {
  overview: () => call<Overview>('GET', '/overview'),
  service: (name: string, action: 'start' | 'stop' | 'restart') => call<any>('POST', `/services/${name}/${action}`),
  startAll: () => call<Record<string, string>>('POST', '/start-all'),
  stopAll: () => call<Record<string, string>>('POST', '/stop-all'),
  role: (service: string, role: string, action: 'start' | 'stop') =>
    call<any>('POST', `/services/${service}/roles/${role}/${action}`),
  runJob: (service: string, job: string) => call<any>('POST', `/services/${service}/jobs/${job}/run`),
  retryConsumer: (service: string, consumer: string) =>
    call<any>('POST', `/services/${service}/consumers/${encodeURIComponent(consumer)}/retry`),
  skipEvent: (service: string, consumer: string, eventId: number) =>
    call<any>('POST', `/services/${service}/consumers/${encodeURIComponent(consumer)}/skip${q({ event_id: eventId })}`),
  ready: (service: string) => call<{ service: string; ready: boolean; checks: Record<string, unknown> }>('GET', `/services/${service}/ready`),
  logs: (service: string, lines: number, console = false) =>
    call<{ lines: string[] }>('GET', service === 'supervisor'
      ? `/logs/supervisor${q({ lines })}` : `/services/${service}/logs${q({ lines, console })}`),
  jobs: (service?: string, limit = 200) => call<{ runs: JobRun[] }>('GET', `/jobs${q({ service, limit })}`),
  events: (limit = 50, type?: string) =>
    call<{ consumers: ConsumerRow[]; recent: EventRow[] }>('GET', `/events${q({ limit, event_type: type })}`),
  database: () => call<{ schemas: SchemaInfo[]; migrations: MigrationStatus[] }>('GET', '/database'),
  migrate: () => call<{ applied: any[] }>('POST', '/database/migrate'),
  config: () => call<any>('GET', '/config'),
  reloadConfig: () => call<{ reloaded: boolean }>('POST', '/config/reload'),
  routes: () => call<{ routes: { pattern: string; service: string }[] }>('GET', '/routes'),
};
