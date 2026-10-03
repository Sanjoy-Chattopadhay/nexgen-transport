/**
 * KPI dropdowns: every headline number opens to show what it is made of.
 *
 *   <KPIGrid className="grid ...">
 *     <KPI label="Arrivals" value={…} drill={{ dataset: 'visits', params: { day, preset: 'arrivals' } }} />
 *   </KPIGrid>
 *
 * The grid owns which tile is open and draws the dropdown beneath all its
 * tiles, full width. The dropdown reads GET /api/v1/drill/{dataset} only when
 * opened, a page at a time: a breakdown of the number (by transporter, site,
 * vehicle, day, hour…) beside the records behind it. Clicking a breakdown
 * narrows the records to it; clicking a record opens its page.
 */
import { useCallback, useMemo, useState, type ReactNode } from 'react';
import { useNavigate } from 'react-router-dom';
import { BarChart3, ListFilter, X } from 'lucide-react';
import { api, type Params } from '../lib/api';
import { useApi } from '../hooks/useApi';
import { KPIGridContext, type DrillSpec, type OpenKPI } from './kpiContext';
import { Badge, DataTable, ErrorBox, Pagination, QualityBadge, ScaleBadge, Spinner, TripRef, type Column } from './ui';
import { PALETTE } from './charts';
import { PhaseBar } from './trip/PhaseBar';
import {
  KIND_LABEL, fmtDateTime, fmtDay, fmtDuration, fmtHours, fmtInt, fmtKm, fmtMetres, fmtNum,
} from '../lib/format';

export type { DrillSpec } from './kpiContext';

// ---------------------------------------------------------------------------
// the grid
// ---------------------------------------------------------------------------

export function KPIGrid({ className = '', children }: { className?: string; children: ReactNode }) {
  const [open, setOpen] = useState<OpenKPI | null>(null);
  const toggle = useCallback((k: OpenKPI) => setOpen(o => (o?.id === k.id ? null : k)), []);
  const ctx = useMemo(() => ({ open, toggle }), [open, toggle]);
  return (
    <KPIGridContext.Provider value={ctx}>
      <div className={className}>
        {children}
        {open && (
          <div className="col-span-full animate-fade-in">
            <DrillPanel key={open.id} kpi={open} onClose={() => setOpen(null)} />
          </div>
        )}
      </div>
    </KPIGridContext.Provider>
  );
}

// ---------------------------------------------------------------------------
// the dropdown
// ---------------------------------------------------------------------------

const GROUP_LABEL: Record<string, string> = {
  transporter: 'Transporter', vehicle: 'Vehicle', driver: 'Driver', lane: 'Lane', quality: 'GPS quality',
  status: 'Status', day: 'Day', hour: 'Hour', weekday: 'Weekday', site: 'Site', kind: 'Kind', scale: 'Scale',
  first_place: 'First place', last_place: 'Last place', shape: 'Trip shape', confirmed: 'Confirmed by',
  reason: 'Reason', state: 'State', district: 'District', type: 'Type', category: 'Category', nh: 'Highway',
  route: 'Road path', spot: 'Spot (~200 m)', verdict: 'Verdict', mode: 'Plan', from_site: 'From', to_site: 'To',
};

const SECONDS = new Set(['dwell', 'duration', 'seconds']);

function fmtMeasure(measure: string | undefined, v: any): string {
  if (v == null) return '—';
  if (measure && SECONDS.has(measure)) return fmtHours(Number(v), Number(v) >= 36000 ? 0 : 1);
  if (measure === 'km' || measure === 'extra_km') return fmtKm(Number(v));
  if (measure === 'inr' || measure === 'variance') return fmtInr(Number(v));
  return typeof v === 'number' ? fmtNum(v, 1) : String(v);
}

export function fmtInr(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return '—';
  const a = Math.abs(v);
  const s = a >= 1e7 ? `${(a / 1e7).toFixed(2)} Cr` : a >= 1e5 ? `${(a / 1e5).toFixed(2)} L` : Math.round(a).toLocaleString('en-IN');
  return `${v < 0 ? '−' : ''}₹${s}`;
}

function groupLabel(group: string, label: any): string {
  if (label == null || label === '—') return '—';
  if (group === 'day') return fmtDay(String(label));
  if (group === 'hour') return `${String(label).padStart(2, '0')}:00`;
  if (group === 'kind') return KIND_LABEL[label] || DEVIATION_LABEL[label] || String(label).replace(/_/g, ' ');
  return String(label);
}

export function DrillPanel({ kpi, onClose }: { kpi: OpenKPI; onClose: () => void }) {
  if (!kpi.drill) {
    const body = typeof kpi.details === 'function' ? (kpi.details as () => ReactNode)() : kpi.details;
    return (
      <PanelFrame kpi={kpi} onClose={onClose}>
        <div className="p-4">{body}</div>
      </PanelFrame>
    );
  }
  return <SqlDrill kpi={kpi} spec={kpi.drill} onClose={onClose} />;
}

function PanelFrame({ kpi, onClose, meta, children }: {
  kpi: OpenKPI; onClose: () => void; meta?: ReactNode; children: ReactNode;
}) {
  return (
    <section className="bg-gray-900 rounded-xl border border-blue-500/30 shadow-lg shadow-black/20 mb-1">
      <div className="flex items-start justify-between gap-3 px-4 pt-3 pb-2 border-b border-gray-800">
        <div className="min-w-0">
          <p className="text-xs uppercase tracking-wider text-gray-500">What makes up</p>
          <h3 className="text-sm font-semibold text-white flex items-center gap-2 flex-wrap">
            {kpi.label} <span className="text-blue-400 tabular">{kpi.value}</span>
          </h3>
          {meta && <div className="text-xs text-gray-500 mt-0.5">{meta}</div>}
        </div>
        <button onClick={onClose} className="p-1 rounded-md text-gray-500 hover:text-gray-200 hover:bg-gray-800" title="Close">
          <X className="w-4 h-4" />
        </button>
      </div>
      {children}
    </section>
  );
}

function SqlDrill({ kpi, spec, onClose }: { kpi: OpenKPI; spec: DrillSpec; onClose: () => void }) {
  const navigate = useNavigate();
  const [group, setGroup] = useState<string | undefined>(spec.groups?.[0]);
  const [gk, setGk] = useState<string | undefined>(undefined);
  const [page, setPage] = useState(1);
  const [sort, setSort] = useState<string | undefined>(spec.sort);
  const [order, setOrder] = useState<'asc' | 'desc'>(spec.order || 'desc');
  const params: Params = { ...(spec.params || {}), group, gk, page, page_size: 10, sort, order };
  const d = useApi(() => api.drill(spec.dataset, params), [spec.dataset, JSON.stringify(params)]);
  const data = d.data;
  const measure = data?.measure ?? (spec.params?.measure as string | undefined);
  const cols = columnsFor(spec.dataset, data?.metric);
  const groups: string[] = spec.groups ?? data?.group_options ?? [];

  const onSort = (key: string) => {
    if (key === sort) setOrder(o => (o === 'asc' ? 'desc' : 'asc'));
    else { setSort(key); setOrder('desc'); }
    setPage(1);
  };
  const rowLink = linkFor(spec.dataset);

  const meta = data && (
    <span className="flex items-center gap-2 flex-wrap">
      <span>{fmtInt(data.rows_total)} {data.title?.toLowerCase() || 'records'}</span>
      {data.groups_total != null && group && (
        <span>· {fmtInt(data.groups_total)} {(GROUP_LABEL[group] || group).toLowerCase()}{data.groups_total === 1 ? '' : 's'}</span>
      )}
      {measure && measure !== 'count' && <span>· total {fmtMeasure(measure, data.value)}</span>}
      {spec.note && <span className="text-gray-600">· {spec.note}</span>}
    </span>
  );

  return (
    <PanelFrame kpi={kpi} onClose={onClose} meta={meta}>
      {d.error ? <div className="p-4"><ErrorBox error={d.error} onRetry={d.reload} /></div> : !data ? <Spinner /> : (
        <div className="grid grid-cols-1 xl:grid-cols-3 gap-0">
          <div className="p-4 xl:border-r border-gray-800 min-w-0">
            {data.stats && <Distribution stats={data.stats} />}
            {groups.length > 0 && (
              <>
                <div className="flex items-center gap-1.5 text-xs text-gray-500 mb-2 mt-1">
                  <BarChart3 className="w-3.5 h-3.5" /> Split by
                </div>
                <div className="flex flex-wrap gap-1 mb-3">
                  {groups.map(g => (
                    <button key={g} onClick={() => { setGroup(g); setGk(undefined); setPage(1); }}
                      className={`px-2 py-1 rounded-md text-xs font-medium transition-colors ${
                        g === group ? 'bg-blue-600/15 text-blue-400' : 'bg-gray-800/60 text-gray-400 hover:text-gray-200'}`}>
                      {GROUP_LABEL[g] || g}
                    </button>
                  ))}
                </div>
                <div className="space-y-1">
                  {(data.groups || []).map((g: any) => {
                    const active = gk === g.key;
                    const max = Math.max(1, ...(data.groups || []).map((x: any) => Number(x.value) || 0));
                    return (
                      <button key={g.key} onClick={() => { setGk(active ? undefined : g.key); setPage(1); }}
                        className={`w-full relative rounded-md overflow-hidden text-left ${active ? 'ring-1 ring-blue-500/60' : 'hover:bg-gray-800/60'}`}>
                        <div className="absolute inset-y-0 left-0 bg-blue-600/15" style={{ width: `${(100 * (Number(g.value) || 0)) / max}%` }} />
                        <div className="relative flex items-center justify-between gap-2 px-2 py-1.5 text-xs">
                          <span className="text-gray-200 truncate min-w-0" title={String(g.label)}>{groupLabel(group!, g.label)}</span>
                          <span className="text-gray-400 tabular shrink-0">
                            {fmtMeasure(measure, g.value)}
                            {g.share != null && <span className="text-gray-600 ml-1.5">{g.share}%</span>}
                          </span>
                        </div>
                      </button>
                    );
                  })}
                  {data.groups_total != null && data.groups_total > (data.groups || []).length && (
                    <p className="text-xs text-gray-600 pt-1">top {data.groups.length} of {fmtInt(data.groups_total)}</p>
                  )}
                </div>
              </>
            )}
          </div>
          <div className="xl:col-span-2 p-4 min-w-0">
            <div className="flex items-center justify-between mb-2 text-xs text-gray-500">
              <span className="flex items-center gap-1.5">
                <ListFilter className="w-3.5 h-3.5" />
                {gk !== undefined && group
                  ? <>only <b className="text-gray-300">{groupLabel(group, data.groups?.find((g: any) => g.key === gk)?.label ?? gk)}</b>
                    <button className="text-blue-400 ml-1" onClick={() => setGk(undefined)}>show all</button></>
                  : 'every record'}
              </span>
              <span>{fmtInt(data.total)} rows</span>
            </div>
            <DataTable dense rows={data.items || []} loading={d.loading} columns={cols}
              sort={sort} order={order} onSort={data.sorts ? onSort : undefined}
              onRowClick={rowLink ? (r => { const to = rowLink(r); if (to) navigate(to); }) : undefined}
              empty="Nothing here" />
            <Pagination page={data.page} pages={data.pages} total={data.total} onPage={setPage} />
          </div>
        </div>
      )}
    </PanelFrame>
  );
}

/** A duration distribution: percentiles and a histogram. */
function Distribution({ stats }: { stats: any }) {
  const bins: any[] = stats.histogram || [];
  const max = Math.max(1, ...bins.map(b => b.n));
  return (
    <div className="mb-4">
      <div className="grid grid-cols-3 gap-2 mb-3">
        {[['10%', stats.p10], ['median', stats.p50], ['90%', stats.p90]].map(([k, v]) => (
          <div key={k as string} className="bg-gray-800/50 rounded-md px-2 py-1.5">
            <p className="text-xs text-gray-500">{k}</p>
            <p className="text-sm font-semibold text-gray-100 tabular">{fmtDuration(v as number)}</p>
          </div>
        ))}
      </div>
      <div className="flex items-end gap-[2px] h-20" title={`${stats.n} samples`}>
        {bins.map((b, i) => (
          <div key={i} className="flex-1 rounded-t-sm min-w-[3px]"
            title={`${fmtDuration(b.from)} – ${b.to == null ? 'more' : fmtDuration(b.to)}: ${b.n}`}
            style={{ height: `${Math.max(2, (100 * b.n) / max)}%`, background: b.to == null ? PALETTE.amber : PALETTE.blue, opacity: 0.8 }} />
        ))}
      </div>
      <div className="flex justify-between text-xs text-gray-600 mt-1">
        <span>{bins.length ? fmtDuration(bins[0].from) : ''}</span>
        <span>{fmtInt(stats.n)} samples, one per physical event</span>
        <span>{bins.length ? (bins[bins.length - 1].to == null ? `${fmtDuration(bins[bins.length - 1].from)}+` : fmtDuration(bins[bins.length - 1].to)) : ''}</span>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// records: columns and links per dataset
// ---------------------------------------------------------------------------

export const DEVIATION_LABEL: Record<string, string> = {
  detour: 'Detour', shortcut: 'Shortcut', off_route_stop: 'Off-route stop', excursion: 'Excursion',
  backtrack: 'Backtrack', alternate_route: 'Alternate route', reroute: 'Rerouted',
};

const vehicleCol: Column<any> = { key: 's_asset_id', label: 'Vehicle', render: r => <span className="text-gray-100">{r.s_asset_id || '—'}</span> };
const tripCol: Column<any> = { key: 'i_trip_no', label: 'Trip', render: r => <TripRef trip={r.i_trip_no} trips={r.s_trips ?? r.s_sibling_trips} /> };
const transCol: Column<any> = { key: 's_trans_name', label: 'Transporter', render: r => <span className="text-xs text-gray-400">{r.s_trans_name || '—'}</span> };
const siteCol: Column<any> = { key: 's_site_name', label: 'Site', render: r => <span className="text-gray-200 text-xs">{r.s_site_name || '—'}</span> };

function columnsFor(dataset: string, metric?: string): Column<any>[] {
  switch (dataset) {
    case 'trips': return [
      { ...tripCol, sortable: true, key: 'trip' },
      vehicleCol, transCol,
      { key: 'lane', label: 'Lane', render: r => <span className="text-xs text-gray-400">{r.s_origin || '?'} → {r.s_destination || '?'}</span> },
      { key: 'start', label: 'GPS from', sortable: true, render: r => <span className="text-xs">{fmtDateTime(r.dt_first_ping)}</span> },
      { key: 'bar', label: 'Loading · transit · unloading', render: r => <PhaseBar trip={r} width={160} /> },
      { key: 'visits', label: 'Visits', align: 'right', sortable: true, render: r => fmtInt(r.share_visits) },
      { key: 'dwell', label: 'At facilities', align: 'right', sortable: true, render: r => fmtDuration(r.share_dwell_s) },
      { key: 'km', label: 'Km', align: 'right', sortable: true, render: r => fmtKm(r.share_km) },
      { key: 'alerts', label: 'Alerts', align: 'right', sortable: true, render: r => r.share_alerts ? <span className="text-red-400">{r.share_alerts}</span> : '0' },
      { key: 'quality', label: 'GPS', render: r => <QualityBadge quality={r.s_quality} reason={r.s_quality_reason} /> },
    ];
    case 'visits': return [
      siteCol, { key: 's_scale', label: 'Scale', render: r => <ScaleBadge scale={r.s_scale} /> },
      vehicleCol,
      { key: 'enter', label: 'Entered', sortable: true, render: r => <span className="text-xs">{r.b_entry_observed ? '' : '≤ '}{fmtDateTime(r.dt_enter)}</span> },
      { key: 'exit', label: 'Left', render: r => r.b_open ? <span className="text-amber-400 text-xs">still inside</span> : <span className="text-xs">{fmtDateTime(r.dt_exit)}</span> },
      { key: 'dwell', label: 'Stay', align: 'right', sortable: true, render: r => `${fmtDuration(r.i_dwell_seconds)}${r.b_open ? '+' : ''}` },
      transCol, tripCol,
    ];
    case 'places': return [
      vehicleCol, { key: 's_site_name', label: 'Place', render: r => <span className="text-gray-200 text-xs">{r.s_site_name}</span> },
      { key: 'start', label: 'From', sortable: true, render: r => <span className="text-xs">{fmtDateTime(r.dt_start)}</span> },
      { key: 'end', label: 'To', render: r => <span className="text-xs">{r.b_open ? <span className="text-amber-400">still there</span> : fmtDateTime(r.dt_end)}</span> },
      { key: 'duration', label: 'Counted', align: 'right', sortable: true, title: 'Time counted in this number (clipped to the day)', render: r => fmtDuration(r.i_seconds) },
      { key: 'stay', label: 'Whole stay', align: 'right', render: r => <span className="text-gray-500">{fmtDuration(r.i_dwell_s)}</span> },
      transCol, tripCol,
    ];
    case 'alerts': return [
      { key: 'time', label: 'When', sortable: true, render: r => <span className="text-xs">{fmtDateTime(r.dt_event)}</span> },
      { key: 's_kind', label: 'What', render: r => <span className={r.s_kind === 'overspeed' ? 'text-amber-400 text-xs' : 'text-red-400 text-xs'}>{KIND_LABEL[r.s_kind] || r.s_kind}</span> },
      siteCol, vehicleCol,
      { key: 'excess', label: 'Speed', align: 'right', sortable: true, render: r => r.i_observed ? `${r.i_observed}/${r.i_limit}` : '—' },
      transCol, tripCol,
    ];
    case 'stops': return [
      vehicleCol,
      { key: 'start', label: 'From', sortable: true, render: r => <span className="text-xs">{fmtDateTime(r.dt_start)}</span> },
      { key: 'duration', label: 'For', align: 'right', sortable: true, render: r => fmtDuration(r.i_duration_s) },
      { key: 'where', label: 'Where', render: r => r.s_site_name ? <span className="text-xs">{r.s_site_name}</span>
        : <span className="font-mono text-xs text-gray-400">{Number(r.d_lat).toFixed(4)}, {Number(r.d_long).toFixed(4)}</span> },
      transCol, tripCol,
    ];
    case 'moving_gaps':
    case 'gaps': return [
      vehicleCol,
      { key: 'start', label: 'Silent from', sortable: true, render: r => <span className="text-xs">{fmtDateTime(r.dt_from)}</span> },
      { key: 'duration', label: 'For', align: 'right', sortable: true, render: r => fmtDuration(r.i_gap_s) },
      { key: 'moved', label: 'Moved', align: 'right', sortable: true, render: r => fmtMetres(r.d_straight_m) },
      { key: 's_kind', label: 'Kind', render: r => <Badge variant={r.s_kind === 'still' ? 'neutral' : 'warning'}>{r.s_kind || 'moving'}</Badge> },
      transCol, tripCol,
    ];
    case 'feed': return [
      vehicleCol, { key: 'day', label: 'Day', sortable: true, render: r => <span className="text-xs">{fmtDay(r.d_day)}</span> },
      { key: 'pings', label: 'Fixes', align: 'right', sortable: true, render: r => fmtInt(r.i_pings) },
      { key: 'rejected', label: 'Refused', align: 'right', sortable: true, render: r => fmtInt(r.i_rejected) },
      { key: 'spikes', label: 'Spikes', align: 'right', sortable: true, render: r => fmtInt(r.i_spikes) },
      transCol, tripCol,
    ];
    case 'rejects': return [
      tripCol, vehicleCol, { key: 's_reason', label: 'Reason', render: r => r.s_reason.replace(/_/g, ' ') },
      { key: 'count', label: 'Fixes', align: 'right', sortable: true, render: r => fmtInt(r.i_count) },
      { key: 'pings', label: 'Of', align: 'right', render: r => fmtInt(r.i_pings_read) }, transCol,
    ];
    case 'fences': return [
      { key: 'name', label: 'Geofence', sortable: true, render: r => <span className="text-gray-100 text-xs">{r.s_site_name}</span> },
      { key: 's_scale', label: 'Scale', render: r => <ScaleBadge scale={r.s_scale} /> },
      { key: 'state', label: 'State', render: r => <span className="text-xs text-gray-400">{r.s_state || '—'}{r.s_district ? ` · ${r.s_district}` : ''}</span> },
      { key: 'visits', label: 'Visits', align: 'right', sortable: true, render: r => fmtInt(r.i_visits) },
      { key: 'dwell', label: 'Hours inside', align: 'right', sortable: true, render: r => fmtHours(r.i_dwell_total_s) },
      { key: 'radius', label: 'Inradius', align: 'right', sortable: true, render: r => fmtMetres(r.d_inradius_m) },
      { key: 'alerts', label: 'Alerts', align: 'right', sortable: true, render: r => fmtInt(r.i_violations) },
    ];
    case 'fence_days': return [
      { key: 'name', label: 'Site', sortable: true, render: r => <span className="text-gray-100 text-xs">{r.s_site_name}</span> },
      { key: 's_scale', label: 'Scale', render: r => <ScaleBadge scale={r.s_scale} /> },
      { key: 'vehicles', label: 'Vehicles', align: 'right', sortable: true, render: r => fmtInt(r.i_vehicles) },
      { key: 'entries', label: 'Arrivals', align: 'right', sortable: true, render: r => fmtInt(r.i_entries) },
      { key: 'dwell', label: 'Hours inside', align: 'right', sortable: true, render: r => fmtHours(r.i_dwell_s) },
      { key: 'p50', label: 'Median stay', align: 'right', render: r => fmtDuration(r.i_dwell_p50_s) },
    ];
    case 'tolls': return [
      { key: 'name', label: 'Plaza', sortable: true, render: r => <span className="text-gray-100 text-xs">{r.s_name}</span> },
      { key: 'state', label: 'State', sortable: true, render: r => <span className="text-xs">{r.s_state}</span> },
      { key: 's_district', label: 'District', render: r => <span className="text-xs text-gray-400">{r.s_district || '—'}</span> },
      { key: 's_nh', label: 'NH', render: r => <span className="text-xs">{r.s_nh || '—'}</span> },
      { key: 's_section', label: 'Section', render: r => <span className="text-xs text-gray-500 line-clamp-1">{r.s_section || '—'}</span> },
    ];
    case 'legs': return [
      vehicleCol,
      { key: 's_site_name', label: metric === 'transit' ? 'From → to' : 'Place', render: r => <span className="text-xs text-gray-200">{r.s_site_name || '—'}</span> },
      { key: 'start', label: metric === 'gateout' ? 'Gate-out stamp' : 'From', sortable: true, render: r => <span className="text-xs">{fmtDateTime(r.dt_start)}</span> },
      { key: 'end', label: metric === 'gateout' ? 'Actually left' : 'To', render: r => <span className="text-xs">{fmtDateTime(r.dt_end)}</span> },
      { key: 'duration', label: 'Time', align: 'right', sortable: true, render: r => <span className="text-gray-100 font-medium">{fmtDuration(r.i_seconds)}</span> },
      transCol, tripCol,
    ];
    case 'route_trips': return [
      tripCol, vehicleCol,
      { key: 'lane', label: 'From → to', render: r => <span className="text-xs text-gray-400">{r.s_from_site || '?'} → {r.s_to_site || '?'}</span> },
      { key: 'planned', label: 'Plan', align: 'right', sortable: true, render: r => fmtKm(r.d_planned_km) },
      { key: 'actual', label: 'Driven', align: 'right', sortable: true, render: r => fmtKm(r.d_actual_km) },
      { key: 'extra', label: 'Extra', align: 'right', sortable: true, render: r => <ExtraKm km={r.d_extra_km} pct={r.d_extra_pct} /> },
      { key: 'deviations', label: 'Deviations', align: 'right', sortable: true, render: r => fmtInt(r.i_deviations) },
      { key: 'variance', label: 'vs plan', align: 'right', sortable: true, render: r => <Inr v={r.d_variance} /> },
    ];
    case 'route_deviations': return [
      tripCol, vehicleCol,
      { key: 's_kind', label: 'Kind', render: r => <DeviationBadge kind={r.s_kind} /> },
      { key: 'start', label: 'Left the route', sortable: true, render: r => <span className="text-xs">{fmtDateTime(r.dt_leave)}</span> },
      { key: 'duration', label: 'For', align: 'right', sortable: true, render: r => fmtDuration(r.i_duration_s) },
      { key: 'offset', label: 'Furthest', align: 'right', sortable: true, render: r => fmtMetres(r.d_max_offset_m) },
      { key: 'extra', label: 'Extra', align: 'right', sortable: true, render: r => <ExtraKm km={r.d_extra_km} /> },
      { key: 'stop', label: 'Stopped', align: 'right', render: r => fmtDuration(r.i_stop_s) },
    ];
    default: return [];
  }
}

function linkFor(dataset: string): ((r: any) => string | null) | null {
  switch (dataset) {
    case 'fences':
    case 'fence_days': return r => `/geo/geofences/${r.i_site_id}`;
    case 'tolls': return null;
    default: return r => (r.i_trip_no ? `/geo/trips/${r.i_trip_no}` : null);
  }
}

export function Inr({ v }: { v: number | null | undefined }) {
  if (v == null) return <span className="text-gray-600">—</span>;
  return <span className={`tabular ${v < 0 ? 'text-red-400' : v > 0 ? 'text-emerald-400' : 'text-gray-400'}`}>{v > 0 ? '+' : ''}{fmtInr(v)}</span>;
}

export function ExtraKm({ km, pct }: { km: number | null | undefined; pct?: number | null }) {
  if (km == null) return <span className="text-gray-600">—</span>;
  const cls = km > 0.5 ? 'text-amber-400' : km < -0.5 ? 'text-emerald-400' : 'text-gray-400';
  return <span className={`tabular ${cls}`}>{km > 0 ? '+' : ''}{fmtKm(km)}{pct != null && <span className="text-gray-600 text-xs ml-1">{pct > 0 ? '+' : ''}{pct}%</span>}</span>;
}

export function DeviationBadge({ kind }: { kind: string }) {
  const v = { detour: 'warning', shortcut: 'success', off_route_stop: 'purple', excursion: 'info', backtrack: 'danger',
    alternate_route: 'cyan', reroute: 'info' }[kind] || 'neutral';
  return <Badge variant={v}>{DEVIATION_LABEL[kind] || kind}</Badge>;
}

// ---------------------------------------------------------------------------
// a paged list anywhere on a page, from the same endpoint
// ---------------------------------------------------------------------------

/**
 * A card's table read a page at a time from a drill dataset -- how the day
 * summary's alerts and trips and the entity pages' trip lists stay light
 * however much history there is.
 */
export function PagedDrillTable({ dataset, params, sort, order = 'desc', pageSize = 10, empty, columns, onRow }: {
  dataset: string; params: Params; sort?: string; order?: 'asc' | 'desc'; pageSize?: number;
  empty?: ReactNode; columns?: Column<any>[]; onRow?: (r: any) => void;
}) {
  const navigate = useNavigate();
  const [page, setPage] = useState(1);
  const [s, setS] = useState(sort);
  const [o, setO] = useState<'asc' | 'desc'>(order);
  const key = JSON.stringify(params);
  const d = useApi(() => api.drill(dataset, { ...params, page, page_size: pageSize, sort: s, order: o }),
    [dataset, key, page, pageSize, s, o]);
  const link = linkFor(dataset);
  const onSort = (k: string) => {
    if (k === s) setO(x => (x === 'asc' ? 'desc' : 'asc'));
    else { setS(k); setO('desc'); }
    setPage(1);
  };
  if (d.error) return <ErrorBox error={d.error} onRetry={d.reload} />;
  return (
    <>
      <DataTable dense rows={d.data?.items ?? []} loading={d.loading} columns={columns ?? columnsFor(dataset)}
        sort={s} order={o} onSort={onSort} empty={empty}
        onRowClick={onRow ?? (link ? (r => { const to = link(r); if (to) navigate(to); }) : undefined)} />
      <Pagination page={d.data?.page ?? 1} pages={d.data?.pages ?? 1} total={d.data?.total} onPage={setPage} />
    </>
  );
}
