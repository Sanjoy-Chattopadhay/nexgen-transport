import { lazy, Suspense, useMemo, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import {
  AlertTriangle, Box, CalendarDays, ChevronLeft, ChevronRight, CircleDot, Clock, Gauge, Hexagon, MapPinned,
  ShieldAlert, ShieldCheck, Timer, Truck, Building2,
} from 'lucide-react';
import { KPIGrid, PagedDrillTable } from '../components/drill';
import { PhaseBar } from '../components/trip/PhaseBar';
import { api } from '../lib/api';
import { useApi } from '../hooks/useApi';
import {
  Card, DataTable, EntityLink, ErrorBox, KPI, Note, PageHeader, QualityBadge, ScaleBadge, Select, Spinner,
} from '../components/ui';
import { PALETTE, SeriesChart } from '../components/charts';

const FleetSkyline = lazy(() => import('../components/three/FleetSkyline'));
import {
  KIND_LABEL, fmtDay, fmtDuration, fmtHours, fmtInt, fmtPct, fmtTime, fmtDateTime, isNum, share, fmtDayShort,
} from '../lib/format';

function delta(today: number | undefined, avg: number | undefined | null): number | null {
  if (!isNum(today) || !isNum(avg) || avg <= 0) return null;
  return (100 * (today - avg)) / avg;
}

export default function DaySummary() {
  const { day: routeDay } = useParams();
  const navigate = useNavigate();
  const days = useApi(() => api.days(), []);
  const list: any[] = days.data?.days ?? [];
  const day: string | undefined = routeDay || days.data?.latest || undefined;
  const detail = useApi(() => (day ? api.day(day) : Promise.resolve(null)), [day]);

  const idx = list.findIndex(d => d.d_day === day);
  const go = (d: string) => navigate(`/geo/day/${d}`);
  const [show3d, setShow3d] = useState(false);
  const hourly3d = useApi(() => (show3d ? api.daysHourly() : Promise.resolve(null)), [show3d]);

  const trend = useMemo(() => list.map(d => ({
    d_day: d.d_day,
    visits: d.i_facility_visits,
    vehicles: d.i_vehicles,
    alerts: d.i_restricted + d.i_overspeed,
  })), [list]);

  if (days.loading && !days.data) return <Spinner label="Loading days" />;
  if (days.error) return <ErrorBox error={days.error} onRetry={days.reload} />;
  if (!list.length) {
    return (
      <>
        <PageHeader title="Day summary" />
        <Note tone="warn">No run has been published yet. Run <code>python -m geofencing.cli run --publish</code>.</Note>
      </>
    );
  }

  const d = detail.data;
  const s = d?.summary;
  const a = d?.avg7;
  const hourly = d?.hourly
    ? Array.from({ length: 24 }, (_, h) => ({
      hour: `${String(h).padStart(2, '0')}:00`,
      entries: d.hourly.entries?.[h] ?? 0, exits: d.hourly.exits?.[h] ?? 0, alerts: d.hourly.alerts?.[h] ?? 0,
    }))
    : [];

  return (
    <div className="animate-fade-in">
      <PageHeader
        title={day ? fmtDay(day) : 'Day summary'}
        subtitle="Everything the fleet did at geofences on one calendar day: where trucks went, how long they stayed, what went wrong, and how far the GPS behind it can be trusted."
        actions={
          <>
            <button disabled={idx <= 0} onClick={() => go(list[idx - 1].d_day)}
              className="p-2 rounded-lg bg-gray-800 hover:bg-gray-700 disabled:opacity-30" title="Previous day">
              <ChevronLeft className="w-4 h-4" />
            </button>
            <Select value={day || ''} onChange={go}
              options={[...list].reverse().map(x => ({ value: x.d_day, label: `${fmtDay(x.d_day)} · ${fmtInt(x.i_vehicles)} vehicles` }))} />
            <button disabled={idx < 0 || idx >= list.length - 1} onClick={() => go(list[idx + 1].d_day)}
              className="p-2 rounded-lg bg-gray-800 hover:bg-gray-700 disabled:opacity-30" title="Next day">
              <ChevronRight className="w-4 h-4" />
            </button>
          </>
        }
      />

      {detail.error && <div className="mb-6"><ErrorBox error={detail.error} onRetry={detail.reload} /></div>}
      {!s && detail.loading && <Spinner label="Loading the day" />}

      {s && (
        <>
          <KPIGrid className="grid grid-cols-2 md:grid-cols-4 2xl:grid-cols-8 gap-3 mb-6">
            <KPI label="Vehicles reporting" value={fmtInt(s.i_vehicles)} icon={Truck} color="blue"
              delta={delta(s.i_vehicles, a?.i_vehicles)} deltaGood="none" hint="vs 7-day avg"
              drill={{ dataset: 'feed', params: { day, measure: 'vehicles' }, groups: ['vehicle', 'transporter'], sort: 'pings',
                note: 'every vehicle with a fix that day' }} />
            <KPI label="Arrivals at facilities" value={fmtInt(s.i_facility_visits)} icon={Hexagon} color="green"
              delta={delta(s.i_facility_visits, a?.i_facility_visits)} deltaGood="none" hint="innermost fence"
              drill={{ dataset: 'visits', params: { day, preset: 'arrivals' }, groups: ['site', 'transporter', 'hour', 'vehicle'], sort: 'enter',
                note: 'innermost facility fence, entry seen, each stay once' }} />
            <KPI label="Sites visited" value={fmtInt(s.i_sites_visited)} icon={MapPinned} color="cyan"
              delta={delta(s.i_sites_visited, a?.i_sites_visited)} deltaGood="none"
              drill={{ dataset: 'fence_days', params: { day, preset: 'facility,present', measure: 'sites' }, groups: ['scale', 'category', 'type'],
                sort: 'vehicles', note: 'facility fences with a vehicle inside at some time that day' }} />
            <KPI label="Hours at facilities" value={fmtHours(s.i_facility_dwell_s, 0)} icon={Clock} color="purple"
              delta={delta(s.i_facility_dwell_s, a?.i_facility_dwell_s)} deltaGood="down" hint="dwell split at midnight"
              drill={{ dataset: 'places', params: { day }, groups: ['site', 'vehicle', 'transporter', 'hour'], sort: 'duration',
                note: 'each vehicle’s stays, nested fences unioned, clipped to the day' }} />
            <KPI label="Restricted-zone entries" value={fmtInt(s.i_restricted)} icon={ShieldAlert}
              color={s.i_restricted ? 'red' : 'gray'} delta={delta(s.i_restricted, a?.i_restricted)} deltaGood="down"
              drill={{ dataset: 'alerts', params: { day, preset: 'restricted' }, groups: ['site', 'kind', 'transporter', 'vehicle'], sort: 'time' }} />
            <KPI label="Overspeed inside sites" value={fmtInt(s.i_overspeed)} icon={Gauge}
              color={s.i_overspeed ? 'amber' : 'gray'} delta={delta(s.i_overspeed, a?.i_overspeed)} deltaGood="down"
              drill={{ dataset: 'alerts', params: { day, preset: 'overspeed' }, groups: ['site', 'transporter', 'vehicle', 'hour'], sort: 'excess' }} />
            <KPI label="Stops outside any fence" value={fmtInt(s.i_stops_outside)} icon={CircleDot} color="amber"
              hint={fmtHours(s.i_stop_outside_s, 0)}
              drill={{ dataset: 'stops', params: { day, preset: 'outside' }, groups: ['spot', 'vehicle', 'transporter', 'hour'], sort: 'duration' }} />
            <KPI label="Moved while untracked" value={fmtInt(s.i_moving_gaps)} icon={Timer} color="gray"
              hint="GPS holes with movement"
              drill={{ dataset: 'moving_gaps', params: { day }, groups: ['vehicle', 'transporter', 'hour'], sort: 'duration' }} />
          </KPIGrid>

          <Card title="The whole feed, hour by hour" icon={Box} className="mb-6"
            subtitle="Every day of the feed as a row of 24 bars, one per hour, as tall as the arrivals at facility fences. Shift patterns, quiet days and the overnight queue stand out; red caps mark the hours with the most alerts. Click a bar to open its day."
            actions={<button onClick={() => setShow3d(v => !v)} className="text-xs text-blue-400 hover:text-blue-300">{show3d ? 'hide' : 'show in 3D'}</button>}>
            {show3d ? (hourly3d.data ? (
              <Suspense fallback={<Spinner label="Loading the 3D view" />}>
                <FleetSkyline days={hourly3d.data.days} selected={day} onPick={go} />
              </Suspense>
            ) : <Spinner label="Loading the feed" />) : (
              <p className="text-xs text-gray-500">A 3D view of {fmtInt(list.length)} days × 24 hours. <button className="text-blue-400" onClick={() => setShow3d(true)}>Show it</button></p>
            )}
          </Card>

          <div className="grid grid-cols-1 xl:grid-cols-5 gap-6 mb-6">
            <Card title="Across the whole feed" icon={CalendarDays} className="xl:col-span-3"
              subtitle="Arrivals at facility fences per day, and vehicles reporting. Click a day to open it.">
              <SeriesChart data={trend} x="d_day" height={220} onClickX={go} highlightX={day}
                xFormat={v => fmtDayShort(v)}
                series={[
                  { key: 'visits', label: 'Arrivals', color: PALETTE.blue },
                  { key: 'vehicles', label: 'Vehicles', color: PALETTE.pair, type: 'line', axis: 'right' },
                ]} />
            </Card>
            <Card title="Hour by hour" icon={Clock} className="xl:col-span-2"
              subtitle="Arrivals and departures at facility fences, and alerts, by hour of the day.">
              <SeriesChart data={hourly} x="hour" height={220} xFormat={v => String(v).slice(0, 2)}
                series={[
                  { key: 'entries', label: 'Arrivals', color: PALETTE.blue },
                  { key: 'exits', label: 'Departures', color: PALETTE.pairAlt },
                  { key: 'alerts', label: 'Alerts', color: PALETTE.red, type: 'line', axis: 'right' },
                ]} />
            </Card>
          </div>

          <div className="grid grid-cols-1 xl:grid-cols-2 gap-6 mb-6">
            <Card title="Busiest sites" icon={Hexagon}
              subtitle="Facility fences by how many vehicles they held today. Every fence is counted, nested or not.">
              <DataTable dense rows={d.top_sites} onRowClick={r => navigate(`/geo/geofences/${r.i_site_id}`)}
                empty="No facility visits on this day"
                columns={[
                  { key: 's_site_name', label: 'Site', render: r => <span className="text-gray-100">{r.s_site_name}</span> },
                  { key: 's_scale', label: 'Scale', render: r => <ScaleBadge scale={r.s_scale} /> },
                  { key: 'i_vehicles', label: 'Vehicles', align: 'right', render: r => fmtInt(r.i_vehicles) },
                  { key: 'i_entries', label: 'Arrivals', align: 'right', render: r => fmtInt(r.i_entries) },
                  { key: 'i_dwell_s', label: 'Hours inside', align: 'right', render: r => fmtHours(r.i_dwell_s) },
                  { key: 'i_dwell_p50_s', label: 'Median stay', align: 'right', render: r => fmtDuration(r.i_dwell_p50_s) },
                ]} />
            </Card>
            <Card title="Longest stays" icon={Timer} iconClass="text-amber-400"
              subtitle="Stays at a facility in progress during the day, longest first. Innermost fence only, so a truck in a yard inside a works is one stay.">
              <DataTable dense rows={d.longest_stays} onRowClick={r => navigate(`/geo/trips/${r.i_trip_no}`)}
                empty="No stays on this day"
                columns={[
                  { key: 's_asset_id', label: 'Vehicle', render: r => <EntityLink to={`/geo/vehicles/${encodeURIComponent(r.s_asset_id)}`}>{r.s_asset_id}</EntityLink> },
                  { key: 's_site_name', label: 'At', render: r => <EntityLink to={`/geo/geofences/${r.i_site_id}`}>{r.s_site_name}</EntityLink> },
                  { key: 'dt_enter', label: 'Since', render: r => fmtDateTime(r.dt_enter) },
                  { key: 'i_dwell_seconds', label: 'Stay', align: 'right',
                    render: r => <span className={r.b_open ? 'text-amber-400' : ''} title={r.b_open ? 'Still inside when the trail ended' : ''}>
                      {fmtDuration(r.i_dwell_seconds)}{r.b_open ? '+' : ''}</span> },
                  { key: 's_trans_name', label: 'Transporter', render: r => <span className="text-gray-400 text-xs">{r.s_trans_name || '—'}</span> },
                ]} />
            </Card>
          </div>

          <div className="grid grid-cols-1 xl:grid-cols-2 gap-6 mb-6">
            <Card title={`Alerts (${fmtInt(s.i_restricted + s.i_overspeed)})`} icon={AlertTriangle} iconClass="text-red-400"
              subtitle="One per breach per visit, never one per GPS fix."
              actions={<EntityLink to={`/geo/alerts?from=${day}&to=${day}`} className="text-xs">All alerts</EntityLink>}>
              <PagedDrillTable dataset="alerts" params={{ day }} sort="time" pageSize={8} empty="No alerts on this day"
                columns={[
                  { key: 'time', label: 'Time', sortable: true, render: r => fmtTime(r.dt_event) },
                  { key: 's_kind', label: 'What', render: r => <span className={r.s_kind === 'overspeed' ? 'text-amber-400' : 'text-red-400'}>{KIND_LABEL[r.s_kind] || r.s_kind}</span> },
                  { key: 's_site_name', label: 'Where', render: r => <EntityLink to={`/geo/geofences/${r.i_site_id}`}>{r.s_site_name}</EntityLink> },
                  { key: 's_asset_id', label: 'Vehicle' },
                  { key: 'excess', label: 'Speed', align: 'right', sortable: true, render: r => r.i_observed ? `${r.i_observed}/${r.i_limit}` : '—' },
                ]} />
            </Card>
            <Card title="Transporters today" icon={Building2}
              subtitle="Arrivals at facility fences today by carrier, with vehicles involved and hours inside.">
              <DataTable dense rows={d.transporters}
                onRowClick={r => navigate(`/geo/transporters/detail?name=${encodeURIComponent(r.transporter)}`)}
                columns={[
                  { key: 'transporter', label: 'Transporter', render: r => <span className="text-gray-100">{r.transporter}</span> },
                  { key: 'vehicles', label: 'Vehicles', align: 'right', render: r => fmtInt(r.vehicles) },
                  { key: 'visits', label: 'Arrivals', align: 'right', render: r => fmtInt(r.visits) },
                  { key: 'dwell_s', label: 'Hours inside', align: 'right', render: r => fmtHours(r.dwell_s) },
                ]} />
            </Card>
          </div>

          <div className="grid grid-cols-1 xl:grid-cols-5 gap-6 mb-6">
            <Card title="Trips on the road" icon={MapPinned} className="xl:col-span-3"
              subtitle="Trips with GPS on this day, those with alerts first. The bar is each trip as a number line: loading, driving, stops, halts, unloading.">
              <PagedDrillTable dataset="trips" params={{ day }} sort="alerts" pageSize={10}
                columns={[
                  { key: 'trip', label: 'Trip', sortable: true, render: r => <span className="text-blue-400 font-medium">{r.i_trip_no}</span> },
                  { key: 's_asset_id', label: 'Vehicle' },
                  { key: 'places', label: 'Places seen', render: r => <span className="text-xs">{r.s_first_site || '—'}{r.s_last_site ? ` → ${r.s_last_site}` : ''}</span> },
                  { key: 'bar', label: 'Loading · transit · unloading', render: r => <PhaseBar trip={r} width={150} /> },
                  { key: 'alerts', label: 'Alerts', align: 'right', sortable: true, render: r => r.i_violations ? <span className="text-red-400">{r.i_violations}</span> : '0' },
                  { key: 's_quality', label: 'GPS', render: r => <QualityBadge quality={r.s_quality} reason={r.s_quality_reason} /> },
                ]} />
            </Card>
            <Card title="Unscheduled stops" icon={CircleDot} iconClass="text-amber-400" className="xl:col-span-2"
              subtitle="The longest standstills outside every facility fence. Many trucks stopping at one spot usually means a missing fence."
              actions={<EntityLink to="/geo/stops" className="text-xs">Hotspots</EntityLink>}>
              <DataTable dense rows={d.stops_outside} onRowClick={r => navigate(`/geo/trips/${r.i_trip_no}`)}
                empty="No stops outside fences"
                columns={[
                  { key: 's_asset_id', label: 'Vehicle' },
                  { key: 'dt_start', label: 'From', render: r => fmtTime(r.dt_start) },
                  { key: 'i_duration_s', label: 'For', align: 'right', render: r => fmtDuration(r.i_duration_s) },
                  { key: 'loc', label: 'Where', render: r => <span className="font-mono text-xs text-gray-400">{Number(r.d_lat).toFixed(4)}, {Number(r.d_long).toFixed(4)}</span> },
                ]} />
            </Card>
          </div>

          <Card title="Can today's numbers be trusted?" icon={ShieldCheck} iconClass="text-emerald-400"
            subtitle="Every figure above rests on these. Refused fixes never reach the detector; corrected spikes are fixes the filter-and-fit stage pulled back to where the truck really was."
            actions={<EntityLink to="/geo/quality" className="text-xs">Data quality</EntityLink>}>
            <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
              <Metric label="GPS fixes" value={fmtInt(s.i_pings)} />
              <Metric label="Refused as impossible" value={fmtInt(s.i_pings_rejected)}
                sub={fmtPct(share(s.i_pings_rejected, s.i_pings), 2)} />
              <Metric label="Spikes corrected" value={fmtInt(s.i_spikes)} sub={fmtPct(share(s.i_spikes, s.i_pings), 2)} />
              <Metric label="Trips on the road" value={fmtInt(s.i_trips)} />
              <Metric label="Trail quality" value={
                <span className="flex flex-wrap gap-1.5">
                  {Object.entries(d.trip_quality || {}).map(([q, n]) => (
                    <span key={q} className="inline-flex items-center gap-1"><QualityBadge quality={q} /><span className="text-xs text-gray-400">{String(n)}</span></span>
                  ))}
                </span>
              } />
            </div>
          </Card>
        </>
      )}
    </div>
  );
}

function Metric({ label, value, sub }: { label: string; value: React.ReactNode; sub?: string }) {
  return (
    <div className="bg-gray-800/40 rounded-lg p-3">
      <p className="text-xs text-gray-500">{label}</p>
      <div className="text-lg font-semibold text-gray-100 tabular mt-0.5">{value}</div>
      {sub && <p className="text-xs text-gray-500">{sub}</p>}
    </div>
  );
}
