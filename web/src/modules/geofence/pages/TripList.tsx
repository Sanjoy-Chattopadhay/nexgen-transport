import { useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { AlertTriangle, Clock, Hexagon, MapPinned, Route, SatelliteDish, Truck } from 'lucide-react';
import { api } from '../lib/api';
import { useApi } from '../hooks/useApi';
import {
  Card, DataTable, DateInput, ErrorBox, KPI, Note, PageHeader, Pagination, QualityBadge, SearchInput, Select, Toolbar,
  TripRef, useSort,
} from '../components/ui';
import { fmtDateTime, fmtDuration, fmtHours, fmtInt, fmtKm, fmtPct, share } from '../lib/format';
import { KPIGrid } from '../components/drill';
import { PhaseBar } from '../components/trip/PhaseBar';

export default function TripList() {
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const siteId = params.get('site_id') || '';
  const [q, setQ] = useState(params.get('q') || '');
  const [status, setStatus] = useState('');
  const [quality, setQuality] = useState('');
  const [alerts, setAlerts] = useState('');
  const [from, setFrom] = useState(params.get('from') || '');
  const [to, setTo] = useState(params.get('to') || '');
  const [page, setPage] = useState(1);
  const { sort, order, onSort } = useSort('start');
  const transporter = params.get('transporter') || '';
  const vehicle = params.get('vehicle') || '';

  const list = useApi(() => api.trips({
    q, status, quality, from, to, page, sort, order, page_size: 25,
    has_alerts: alerts === 'yes' ? true : undefined, site_id: siteId || undefined,
    transporter: transporter || undefined, vehicle: vehicle || undefined,
  }), [q, status, quality, alerts, from, to, page, sort, order, siteId, transporter, vehicle]);
  const k = list.data?.kpis;
  // The dropdowns count what these trips own, as the tiles do.
  const ctx = { q, status, quality, from, to, has_alerts: alerts === 'yes' ? true : undefined, site_id: siteId || undefined,
    transporter: transporter || undefined, vehicle: vehicle || undefined };
  const reset = <T,>(fn: (v: T) => void) => (v: T) => { fn(v); setPage(1); };

  return (
    <div className="animate-fade-in">
      <PageHeader title="Trips"
        subtitle="Every trip with GPS, seen through its geofences: where it went, how long it stayed at each place, what alerts it raised, and how complete its GPS was." />

      {(siteId || transporter || vehicle) && (
        <div className="mb-4">
          <Note>
            Filtered to {siteId && <>trips that visited site <b className="text-gray-200">#{siteId}</b></>}
            {transporter && <>transporter <b className="text-gray-200">{transporter}</b></>}
            {vehicle && <>vehicle <b className="text-gray-200">{vehicle}</b></>}.{' '}
            <button className="text-blue-400 hover:text-blue-300" onClick={() => setParams({})}>Clear</button>
          </Note>
        </div>
      )}

      {k && (
        <KPIGrid className="grid grid-cols-2 md:grid-cols-4 2xl:grid-cols-7 gap-3 mb-6">
          <KPI label="Trips" value={fmtInt(k.trips)} icon={MapPinned} color="blue"
            hint={k.sharing_gps ? `${fmtInt(k.sharing_gps)} share a truck with another consignment` : undefined} 
          drill={{ dataset: 'trips', params: ctx, groups: ['transporter', 'lane', 'quality', 'day'], sort: 'start' }} />
          <KPI label="Vehicles" value={fmtInt(k.vehicles)} icon={Truck} color="green" 
          drill={{ dataset: 'trips', params: { ...ctx, measure: 'vehicles' }, groups: ['vehicle', 'transporter'], sort: 'start' }} />
          <KPI label="Facility visits" value={fmtInt(k.facility_visits)} icon={Hexagon} color="cyan"
            hint="innermost fence, each stay once" 
          drill={{ dataset: 'visits', params: { ...ctx, via: 'trips', preset: 'primary_facility' }, groups: ['site', 'transporter', 'day', 'hour'], sort: 'enter', note: 'each stay once, owned by the lowest-numbered trip that saw it' }} />
          <KPI label="Hours at facilities" value={fmtHours(k.facility_dwell_s, 0)} icon={Clock} color="purple"
            hint={k.distance_km != null ? `${fmtKm(k.distance_km)} driven` : undefined} 
          drill={{ dataset: 'trips', params: { ...ctx, measure: 'dwell' }, groups: ['transporter', 'first_place', 'day'], sort: 'dwell' }} />
          <KPI label="With origin & destination seen" value={fmtInt(k.with_lane)} icon={Route} color="green"
            hint={fmtPct(share(k.with_lane, k.trips), 0)} 
          drill={{ dataset: 'trips', params: { ...ctx, preset: 'with_lane' }, groups: ['lane', 'first_place', 'last_place'], sort: 'start' }} />
          <KPI label="Alerts" value={fmtInt(k.violations)} icon={AlertTriangle} color="red" 
          drill={{ dataset: 'alerts', params: { ...ctx, via: 'trips' }, groups: ['kind', 'site', 'transporter', 'vehicle'], sort: 'time' }} />
          <KPI label="GPS broken" value={fmtInt(k.broken)} icon={SatelliteDish} color="amber"
            hint="moved during an hour-long hole" 
          drill={{ dataset: 'trips', params: { ...ctx, preset: 'broken' }, groups: ['transporter', 'day'], sort: 'moving_gaps' }} />
        </KPIGrid>
      )}

      <Card>
        <div className="mb-4">
          <Toolbar>
            <SearchInput value={q} onChange={reset(setQ)} placeholder="Trip no, vehicle, driver, transporter, place" width="w-80" />
            <Select value={status} onChange={reset(setStatus)}
              options={[{ value: '', label: 'Any status' }, ...(list.data?.statuses ?? []).map((s: string) => ({ value: s, label: s }))]} />
            <Select value={quality} onChange={reset(setQuality)}
              options={[{ value: '', label: 'Any GPS quality' }, ...['good', 'sparse', 'noisy', 'broken', 'no_gps'].map(s => ({ value: s, label: s }))]} />
            <Select value={alerts} onChange={reset(setAlerts)}
              options={[{ value: '', label: 'With or without alerts' }, { value: 'yes', label: 'With alerts' }]} />
            <DateInput label="From" value={from} onChange={reset(setFrom)} />
            <DateInput label="To" value={to} onChange={reset(setTo)} />
          </Toolbar>
        </div>
        {list.error ? <ErrorBox error={list.error} onRetry={list.reload} /> : (
          <>
            <DataTable rows={list.data?.items ?? []} loading={list.loading} sort={sort} order={order}
              onSort={k2 => { onSort(k2); setPage(1); }} rowKey={r => r.i_trip_no}
              onRowClick={r => navigate(`/geo/trips/${r.i_trip_no}`)}
              columns={[
                { key: 'trip', label: 'Trip', sortable: true, render: r => <TripRef trip={r.i_trip_no} trips={r.s_sibling_trips} strong /> },
                { key: 'vehicle', label: 'Vehicle', sortable: true, render: r => (
                  <div><p className="text-gray-100">{r.s_asset_id || '—'}</p><p className="text-xs text-gray-500">{r.s_driver_name || ''}</p></div>
                ) },
                { key: 'transporter', label: 'Transporter', sortable: true, render: r => <span className="text-xs">{r.s_trans_name || '—'}</span> },
                { key: 'lane', label: 'Lane (fleet system)', render: r => (
                  <span className="text-xs text-gray-400">{r.s_origin || '?'} → {r.s_destination || '?'}</span>
                ) },
                { key: 'start', label: 'GPS from', sortable: true, render: r => <span className="text-xs">{fmtDateTime(r.dt_first_ping)}</span> },
                { key: 'places', label: 'Places seen', sortable: true, render: r => (
                  <div className="text-xs max-w-[260px]">
                    <span className="text-gray-200">{r.s_first_site || '—'}</span>
                    {r.s_last_site && <><span className="text-gray-600"> → </span><span className="text-gray-200">{r.s_last_site}</span></>}
                    <p className="text-xs text-gray-500">{fmtInt(r.i_places)} places · {fmtInt(r.i_facility_visits)} visits</p>
                  </div>
                ) },
                { key: 'bar', label: 'Loading · transit · unloading', render: r => <PhaseBar trip={r} width={150} /> },
                { key: 'dwell', label: 'At facilities', sortable: true, align: 'right', render: r => fmtDuration(r.i_facility_dwell_s) },
                { key: 'transit', label: 'Transit', sortable: true, align: 'right', title: 'First place exit to last place entry',
                  render: r => fmtDuration(r.i_transit_s) },
                { key: 'distance', label: 'Distance', sortable: true, align: 'right', render: r => fmtKm(r.d_distance_km) },
                { key: 'violations', label: 'Alerts', sortable: true, align: 'right',
                  render: r => r.i_violations ? <span className="text-red-400 font-medium">{r.i_violations}</span> : '0' },
                { key: 'quality', label: 'GPS', sortable: true, render: r => <QualityBadge quality={r.s_quality} reason={r.s_quality_reason} /> },
              ]} />
            <Pagination page={page} pages={list.data?.pages ?? 1} total={list.data?.total} onPage={setPage} />
          </>
        )}
      </Card>
    </div>
  );
}
