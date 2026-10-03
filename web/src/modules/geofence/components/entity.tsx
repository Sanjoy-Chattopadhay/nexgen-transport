/** Building blocks shared by the vehicle, transporter, lane and driver pages. */
import { useNavigate } from 'react-router-dom';
import { AlertTriangle, CalendarDays, Hexagon, MapPinned } from 'lucide-react';
import { Card, DataTable, EntityLink, QualityBadge, ScaleBadge, TripRef, type Column } from './ui';
import { PagedDrillTable } from './drill';
import { PhaseBar } from './trip/PhaseBar';
import type { Params } from '../lib/api';
import { PALETTE, SeriesChart } from './charts';
import { KIND_LABEL, fmtDateTime, fmtDuration, fmtHours, fmtInt, fmtKm, fmtDayShort } from '../lib/format';

/**
 * An entity's trips, read a page at a time (GET /api/v1/drill/trips with the
 * entity as context), so a vehicle with years of trips opens as fast as one
 * with a week. The bar is each trip's number line.
 */
export function TripsCard({ ctx, total, title = 'Trips', hide = [] }: {
  ctx: Params; total?: number; title?: string; hide?: string[];
}) {
  const cols: Column<any>[] = ([
    { key: 'trip', label: 'Trip', sortable: true, render: (r: any) => <TripRef trip={r.i_trip_no} trips={r.s_sibling_trips} strong /> },
    { key: 'asset', label: 'Vehicle', render: (r: any) => r.s_asset_id ?? r.asset },
    { key: 's_trans_name', label: 'Transporter', render: (r: any) => <span className="text-xs">{r.s_trans_name || '—'}</span> },
    { key: 's_driver_name', label: 'Driver', render: (r: any) => <span className="text-xs">{r.s_driver_name || '—'}</span> },
    { key: 'lane', label: 'Lane', render: (r: any) => <span className="text-xs text-gray-400">{r.s_origin || '?'} → {r.s_destination || '?'}</span> },
    { key: 'start', label: 'GPS from', sortable: true, render: (r: any) => <span className="text-xs">{fmtDateTime(r.dt_first_ping)}</span> },
    { key: 'places', label: 'Places seen', render: (r: any) => (
      <span className="text-xs">{r.s_first_site || '—'}{r.s_last_site ? ` → ${r.s_last_site}` : ''}</span>
    ) },
    { key: 'bar', label: 'Loading · transit · unloading', render: (r: any) => <PhaseBar trip={r} width={140} /> },
    { key: 'dwell', label: 'At facilities', align: 'right' as const, sortable: true, render: (r: any) => fmtDuration(r.i_facility_dwell_s) },
    { key: 'transit', label: 'Transit', align: 'right' as const, sortable: true, render: (r: any) => fmtDuration(r.i_transit_s) },
    { key: 'km', label: 'Distance', align: 'right' as const, sortable: true, render: (r: any) => fmtKm(r.d_distance_km) },
    { key: 'alerts', label: 'Alerts', align: 'right' as const, sortable: true, render: (r: any) => r.i_violations ? <span className="text-red-400">{r.i_violations}</span> : '0' },
    { key: 's_quality', label: 'GPS', render: (r: any) => <QualityBadge quality={r.s_quality} /> },
  ] as Column<any>[]).filter(c => !hide.includes(c.key));
  return (
    <Card title={total != null ? `${title} (${fmtInt(total)})` : title} icon={MapPinned}>
      <PagedDrillTable dataset="trips" params={ctx} sort="start" pageSize={15} columns={cols} />
    </Card>
  );
}

export function TopSitesCard({ sites, title = 'Where they go' }: { sites: any[]; title?: string }) {
  const navigate = useNavigate();
  return (
    <Card title={title} icon={Hexagon} subtitle="Facility fences by visits (innermost fence of each stay).">
      <DataTable dense rows={sites} onRowClick={r => navigate(`/geo/geofences/${r.i_site_id}`)} empty="No facility visits"
        columns={[
          { key: 's_site_name', label: 'Site', render: r => <span className="text-gray-100">{r.s_site_name}</span> },
          { key: 's_scale', label: 'Scale', render: r => <ScaleBadge scale={r.s_scale} /> },
          { key: 'visits', label: 'Visits', align: 'right', render: r => fmtInt(r.visits) },
          { key: 'dwell_s', label: 'Hours inside', align: 'right', render: r => fmtHours(r.dwell_s) },
          { key: 'last_visit', label: 'Last', render: r => <span className="text-xs text-gray-400">{fmtDateTime(r.last_visit)}</span> },
        ]} />
    </Card>
  );
}

/** An entity's alerts: those its trips own, each breach once, a page at a time. */
export function AlertsCard({ ctx, total }: { ctx: Params; total?: number }) {
  return (
    <Card title={total != null ? `Alerts (${fmtInt(total)})` : 'Alerts'} icon={AlertTriangle} iconClass="text-red-400">
      <PagedDrillTable dataset="alerts" params={{ ...ctx, via: 'trips' }} sort="time" pageSize={10} empty="No alerts"
        columns={[
          { key: 'time', label: 'When', sortable: true, render: r => fmtDateTime(r.dt_event) },
          { key: 's_kind', label: 'What', render: r => <span className={r.s_kind === 'overspeed' ? 'text-amber-400' : 'text-red-400'}>{KIND_LABEL[r.s_kind] || r.s_kind}</span> },
          { key: 's_site_name', label: 'Where', render: r => <EntityLink to={`/geo/geofences/${r.i_site_id}`}>{r.s_site_name}</EntityLink> },
          { key: 's_asset_id', label: 'Vehicle' },
          { key: 'excess', label: 'Speed', align: 'right', sortable: true, render: r => r.i_observed ? `${r.i_observed}/${r.i_limit}` : '—' },
        ]} />
    </Card>
  );
}

export function DailyCard({ daily }: { daily: any[] }) {
  return (
    <Card title="Day by day" icon={CalendarDays} subtitle="Arrivals at facility fences and hours inside, per day.">
      {daily.length ? (
        <SeriesChart data={daily.map(d => ({ ...d, hours: Math.round((d.dwell_s || 0) / 360) / 10 }))} x="d_day" height={220}
          xFormat={v => fmtDayShort(v)}
          series={[
            { key: 'visits', label: 'Arrivals', color: PALETTE.blue },
            { key: 'hours', label: 'Hours inside', color: PALETTE.purple, type: 'line', axis: 'right' },
          ]} />
      ) : <p className="text-sm text-gray-500 py-8 text-center">No facility visits.</p>}
    </Card>
  );
}
