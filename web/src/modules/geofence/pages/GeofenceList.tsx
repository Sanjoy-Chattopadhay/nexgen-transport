import { useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { AlertTriangle, Clock, Eye, Hexagon, MapPinned, Maximize2, Target } from 'lucide-react';
import { api } from '../lib/api';
import { useApi } from '../hooks/useApi';
import {
  Card, CategoryBadge, DataTable, ErrorBox, KPI, Note, PageHeader, Pagination, ScaleBadge, SearchInput, Select,
  Toolbar, useSort,
} from '../components/ui';
import { KPIGrid } from '../components/drill';
import { fmtArea, fmtDateTime, fmtDuration, fmtHours, fmtInt, fmtMetres } from '../lib/format';

export default function GeofenceList() {
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const [q, setQ] = useState('');
  const [state, setState] = useState(params.get('state') || '');
  const [type, setType] = useState('');
  const [category, setCategory] = useState('');
  const [scale, setScale] = useState('');
  const [visited, setVisited] = useState('all');
  const [status, setStatus] = useState('active');
  const [page, setPage] = useState(1);
  const { sort, order, onSort } = useSort('visits');

  const facets = useApi(() => api.geofenceFacets(), []);
  const list = useApi(() => api.geofences({ q, type, category, scale, visited, status, state, sort, order, page, page_size: 25 }),
    [q, type, category, scale, visited, status, state, sort, order, page]);

  const t = facets.data?.totals;
  const reset = (fn: (v: string) => void) => (v: string) => { fn(v); setPage(1); };

  return (
    <div className="animate-fade-in">
      <PageHeader title="Geofences"
        subtitle="Every fence in the Tata Steel master, with what the fleet actually did at it. Click a fence for its shape, traffic pattern, dwell times and visitors." />

      {t && (
        <KPIGrid className="grid grid-cols-2 md:grid-cols-3 2xl:grid-cols-6 gap-3 mb-6">
          <KPI label="Active fences" value={fmtInt(t.active)} icon={Hexagon} color="blue" hint={`${fmtInt(t.fences)} in master`} 
          drill={{ dataset: 'fences', params: { preset: 'active' }, groups: ['state', 'scale', 'category', 'type'], sort: 'visits' }} />
          <KPI label="Visited in this run" value={fmtInt(t.visited)} icon={Eye} color="green"
            hint={t.active ? `${Math.round((100 * t.visited) / t.active)}% of active` : undefined} 
          drill={{ dataset: 'fences', params: { preset: 'visited' }, groups: ['scale', 'state', 'type'], sort: 'visits' }} />
          <KPI label="Facility visits" value={fmtInt(t.visits)} icon={MapPinned} color="cyan" hint="every nesting level" 
          drill={{ dataset: 'fences', params: { preset: 'facility_stats', measure: 'visits' }, groups: ['scale', 'state', 'type'], sort: 'visits', note: 'every nesting level: a truck in a mill inside a works is a visit to each' }} />
          <KPI label="Hours inside facilities" value={fmtHours(t.dwell_s, 0)} icon={Clock} color="purple"
            hint="each vehicle-hour once" 
          details={() => <DayHours />} />
          <KPI label="Small fences (<25 m radius)" value={fmtInt(t.small)} icon={Target} color="amber"
            hint="detectable with adaptive band" 
          drill={{ dataset: 'fences', params: { preset: 'small' }, groups: ['scale', 'state', 'type'], sort: 'radius', order: 'asc' }} />
          <KPI label="Alerts at fences" value={fmtInt(t.violations)} icon={AlertTriangle} color="red" 
          drill={{ dataset: 'fences', params: { preset: 'facility_stats,alerts', measure: 'alerts' }, groups: ['category', 'state', 'scale'], sort: 'alerts' }} />
        </KPIGrid>
      )}

      <Card>
        <div className="flex items-center justify-between gap-3 flex-wrap mb-4">
          <Toolbar>
            <SearchInput value={q} onChange={reset(setQ)} placeholder="Site name or id…" />
            <Select value={state} onChange={reset(setState)}
              options={[{ value: '', label: 'All states' },
                ...(facets.data?.states ?? []).filter((x: any) => x.s_state)
                  .map((x: any) => ({ value: x.s_state, label: `${x.s_state} · ${x.n}` }))]} />
            <Select value={type} onChange={reset(setType)}
              options={[{ value: '', label: 'All types' },
                ...(facets.data?.types ?? []).map((x: any) => ({ value: x.s_type || '', label: `${x.s_type || '(none)'} · ${x.n}` }))]} />
            <Select value={category} onChange={reset(setCategory)}
              options={[{ value: '', label: 'All categories' },
                ...(facets.data?.categories ?? []).map((x: any) => ({ value: x.s_category, label: `${x.s_category} · ${x.n}` }))]} />
            <Select value={scale} onChange={reset(setScale)}
              options={[{ value: '', label: 'All scales' },
                ...['micro', 'site', 'campus', 'regional'].map(s => ({
                  value: s, label: `${s} · ${facets.data?.scales?.find((x: any) => x.s_scale === s)?.n ?? ''}` }))]} />
            <Select value={visited} onChange={reset(setVisited)}
              options={[{ value: 'all', label: 'Visited or not' }, { value: 'yes', label: 'Visited' }, { value: 'no', label: 'Never visited' }]} />
            <Select value={status} onChange={reset(setStatus)}
              options={[{ value: 'active', label: 'Active' }, { value: 'inactive', label: 'Inactive' }, { value: 'all', label: 'All' }]} />
          </Toolbar>
        </div>

        {list.error ? <ErrorBox error={list.error} onRetry={list.reload} /> : (
          <>
            <DataTable rows={list.data?.items ?? []} loading={list.loading} sort={sort} order={order}
              onSort={k => { onSort(k); setPage(1); }}
              onRowClick={r => navigate(`/geo/geofences/${r.i_site_id}`)}
              rowKey={r => r.i_fence_id}
              columns={[
                { key: 'name', label: 'Site', sortable: true, render: r => (
                  <div className="min-w-[220px]">
                    <p className="text-gray-100 font-medium">{r.s_site_name}</p>
                    <p className="text-xs text-gray-500">#{r.i_site_id} · {r.s_type || 'no type'}</p>
                    <p className="text-xs text-gray-500">{r.s_district ? `${r.s_district}, ` : ''}{r.s_state || 'state unknown'}{r.s_states ? ' · crosses a border' : ''}</p>
                  </div>
                ) },
                { key: 'category', label: 'Category', render: r => <CategoryBadge category={r.s_category} /> },
                { key: 'area', label: 'Size', sortable: true, align: 'right', render: r => (
                  <div className="text-right">
                    <ScaleBadge scale={r.s_scale} />
                    <p className="text-xs text-gray-500 mt-0.5">{fmtArea(r.d_area_sqm)}</p>
                  </div>
                ) },
                { key: 'band', label: 'Band', align: 'right', title: 'Hysteresis band used for this fence',
                  render: r => <span className="text-xs text-gray-400" title={`Inscribed radius ${fmtMetres(r.d_inradius_m)}`}>±{r.band_m} m</span> },
                { key: 'visits', label: 'Visits', sortable: true, align: 'right', render: r => fmtInt(r.i_visits) },
                { key: 'vehicles', label: 'Vehicles', sortable: true, align: 'right', render: r => fmtInt(r.i_vehicles) },
                { key: 'dwell_p50', label: 'Median stay', sortable: true, align: 'right', render: r => fmtDuration(r.i_dwell_p50_s) },
                { key: 'dwell_total', label: 'Hours inside', sortable: true, align: 'right', render: r => fmtHours(r.i_dwell_total_s) },
                { key: 'violations', label: 'Alerts', sortable: true, align: 'right',
                  render: r => r.i_violations ? <span className="text-red-400">{fmtInt(r.i_violations)}</span> : '0' },
                { key: 'last', label: 'Last seen', sortable: true, render: r => <span className="text-xs text-gray-400">{fmtDateTime(r.dt_last)}</span> },
              ]} />
            <Pagination page={page} pages={list.data?.pages ?? 1} total={list.data?.total} onPage={setPage} />
          </>
        )}
      </Card>

      <div className="mt-6">
        <Note title="Why the band column matters." >
          A truck is only counted inside a fence once it is clearly past the boundary, by the band shown. A fixed 25 m
          band made fences smaller than 50 m across undetectable — nothing inside them is ever 25 m from the edge — so
          the band now scales to half of each fence's inscribed radius. <Maximize2 className="inline w-3 h-3" /> Large works are unchanged.
        </Note>
      </div>
    </div>
  );
}

/** Hours at facilities, day by day: the days add up to the tile. */
function DayHours() {
  const days = useApi(() => api.days(), []);
  const rows = [...(days.data?.days ?? [])].sort((a: any, b: any) => (b.i_facility_dwell_s || 0) - (a.i_facility_dwell_s || 0));
  return (
    <div className="max-h-[360px] overflow-y-auto">
      <p className="text-xs text-gray-500 mb-2">Each vehicle's time at facilities once, however many nested fences it was inside, split at midnight. Open a day for its places.</p>
      <DataTable dense rows={rows} rowKey={r => r.d_day}
        onRowClick={r => { window.location.href = `/geo/day/${r.d_day}`; }}
        columns={[
          { key: 'd_day', label: 'Day', render: r => <span className="text-xs">{fmtDateTime(r.d_day).slice(0, 6)} {String(r.d_day).slice(0, 10)}</span> },
          { key: 'h', label: 'Hours at facilities', align: 'right', render: r => fmtHours(r.i_facility_dwell_s, 0) },
          { key: 'v', label: 'Vehicles', align: 'right', render: r => fmtInt(r.i_vehicles) },
          { key: 'a', label: 'Arrivals', align: 'right', render: r => fmtInt(r.i_facility_visits) },
        ]} />
    </div>
  );
}
