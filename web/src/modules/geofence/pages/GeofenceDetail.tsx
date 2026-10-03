import { useEffect, useMemo, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import L from 'leaflet';
import {
  AlertTriangle, BarChart3, Building2, CalendarDays, Clock, Grid3x3, Hexagon, Layers, MapPinned, Route, Ruler,
  Timer, Truck, Users,
} from 'lucide-react';
import { api } from '../lib/api';
import { useApi } from '../hooks/useApi';
import {
  Badge, Card, CategoryBadge, DataTable, DateInput, EntityLink, ErrorBox, Field, KPI, Note, PageHeader, Pagination,
  ScaleBadge, SearchInput, Spinner, Toggle, Toolbar, TripRef, useSort,
} from '../components/ui';
import { KPIGrid } from '../components/drill';
import GeoMap from '../components/map/GeoMap';
import { dot, fencePolygon } from '../components/map/layers';
import { BarList, PALETTE, SeriesChart, WeekHourHeatmap } from '../components/charts';
import {
  KIND_LABEL, fmtArea, fmtDateTime, fmtDuration, fmtHours, fmtInt, fmtMetres, fmtDayShort,
} from '../lib/format';

export default function GeofenceDetail() {
  const { siteId = '' } = useParams();
  const navigate = useNavigate();
  const detail = useApi(() => api.geofence(siteId), [siteId]);
  const activity = useApi(() => api.geofenceActivity(siteId), [siteId]);

  const [map, setMap] = useState<L.Map | null>(null);
  const [showCrossings, setShowCrossings] = useState(true);
  const [showNeighbours, setShowNeighbours] = useState(true);

  const f = detail.data?.fence;
  const neighbours = useApi(
    () => (f ? api.geofencesNear(Number(f.d_centroid_lat), Number(f.d_centroid_long),
      Math.min(20000, Math.max(800, Math.sqrt(Number(f.d_area_sqm)) * 1.5))) : Promise.resolve(null)),
    [f?.i_fence_id]);

  // Fence, context and crossings on the map.
  useEffect(() => {
    if (!map || !detail.data) return;
    const d = detail.data;
    const group = L.layerGroup().addTo(map);
    if (showNeighbours && neighbours.data) {
      for (const n of neighbours.data.fences) {
        if (n.site_id === d.fence.i_site_id || !n.ring?.length) continue;
        fencePolygon(n, { dim: true, onClick: () => navigate(`/geo/geofences/${n.site_id}`) }).addTo(group);
      }
    }
    fencePolygon({
      site_id: d.fence.i_site_id, name: d.fence.s_site_name, category: d.fence.s_category,
      scale: d.fence.s_scale, ring: d.ring,
    }, { focus: true }).addTo(group);
    if (showCrossings && activity.data?.crossings) {
      for (const c of activity.data.crossings) {
        dot(c.lat, c.lon, c.event === 'enter' ? PALETTE.green : PALETTE.red, 3,
          `${c.event === 'enter' ? 'Entry' : 'Exit'} · confirmed by ${c.by}${c.gap > 900 ? ` · ±${fmtDuration(c.gap)}` : ''}`)
          .addTo(group);
      }
    }
    return () => { group.remove(); };
  }, [map, detail.data, activity.data, neighbours.data, showCrossings, showNeighbours, navigate]);

  // Frame the fence once, when it first arrives.
  useEffect(() => {
    if (!map || !detail.data?.ring?.length) return;
    map.fitBounds(L.latLngBounds(detail.data.ring), { padding: [40, 40], maxZoom: 17 });
  }, [map, detail.data?.fence?.i_fence_id]); // eslint-disable-line react-hooks/exhaustive-deps

  if (detail.loading && !detail.data) return <Spinner label="Loading geofence" />;
  if (detail.error) return <ErrorBox error={detail.error} onRetry={detail.reload} />;
  if (!f) return null;

  const st = detail.data.stats;
  const act = activity.data;
  const nesting = detail.data.nesting;
  const regional = f.s_scale === 'regional';

  return (
    <div className="animate-fade-in">
      <PageHeader
        back={{ to: '/geo/geofences', label: 'All geofences' }}
        title={f.s_site_name}
        subtitle={`Site #${f.i_site_id} · ${f.s_type || 'no type'} · ${fmtArea(f.d_area_sqm)}`}
        badges={<>
          <CategoryBadge category={f.s_category} />
          <ScaleBadge scale={f.s_scale} />
          {!f.b_active && <Badge variant="warning">inactive</Badge>}
        </>}
        actions={<EntityLink to={`/geo/trips?site_id=${f.i_site_id}`} className="text-sm">Trips that visited →</EntityLink>}
      />

      {regional && (
        <div className="mb-6">
          <Note tone="warn" title="This is a district-scale catchment, not a facility.">
            A truck can be inside it for hours of ordinary highway driving, so a "visit" and a "stay" here do not mean
            what they mean at a works or a weighbridge.
          </Note>
        </div>
      )}

      <KPIGrid className="grid grid-cols-2 md:grid-cols-4 2xl:grid-cols-8 gap-3 mb-6">
        <KPI label="Visits" value={fmtInt(st?.i_visits ?? 0)} icon={MapPinned} color="blue" 
          drill={{ dataset: 'visits', params: { site_id: siteId }, groups: ['day', 'hour', 'weekday', 'transporter'], sort: 'enter', note: 'every visit to this fence, nested or not' }} />
        <KPI label="Vehicles" value={fmtInt(st?.i_vehicles ?? 0)} icon={Truck} color="green" 
          drill={{ dataset: 'visits', params: { site_id: siteId, measure: 'dwell' }, groups: ['vehicle', 'transporter'], sort: 'dwell', headline: 'groups' }} />
        <KPI label="Trips" value={fmtInt(st?.i_trips ?? 0)} icon={Route} color="cyan" 
          drill={{ dataset: 'trips', params: { site_id: siteId }, groups: ['transporter', 'lane', 'day'], sort: 'start' }} />
        <KPI label="Transporters" value={fmtInt(st?.i_transporters ?? 0)} icon={Building2} color="purple" 
          drill={{ dataset: 'visits', params: { site_id: siteId, measure: 'dwell' }, groups: ['transporter', 'vehicle'], sort: 'dwell' }} />
        <KPI label="Median stay" value={fmtDuration(st?.i_dwell_p50_s)} icon={Timer} color="amber"
          hint="fully observed visits" 
          drill={{ dataset: 'visits', params: { site_id: siteId, preset: 'measured', stats: 'dwell' }, groups: ['transporter', 'weekday', 'hour'], sort: 'dwell', note: 'fully observed stays only: entry and exit both seen' }} />
        <KPI label="90% leave within" value={fmtDuration(st?.i_dwell_p90_s)} icon={Clock} color="amber" 
          drill={{ dataset: 'visits', params: { site_id: siteId, preset: 'measured', stats: 'dwell' }, groups: ['transporter', 'vehicle'], sort: 'dwell' }} />
        <KPI label="Hours inside" value={fmtHours(st?.i_dwell_total_s ?? 0, 0)} icon={Clock} color="purple" 
          drill={{ dataset: 'visits', params: { site_id: siteId, measure: 'dwell' }, groups: ['transporter', 'vehicle', 'day'], sort: 'dwell' }} />
        <KPI label="Alerts" value={fmtInt(st?.i_violations ?? 0)} icon={AlertTriangle}
          color={st?.i_violations ? 'red' : 'gray'} 
          drill={{ dataset: 'alerts', params: { site_id: siteId }, groups: ['kind', 'transporter', 'vehicle', 'hour'], sort: 'time' }} />
      </KPIGrid>

      <div className="grid grid-cols-1 xl:grid-cols-3 gap-6 mb-6">
        <Card title="Shape on the map" icon={Hexagon} className="xl:col-span-2"
          subtitle="The fence exactly as its vertices define it. Green dots are where trucks were confirmed entering, red where they left — the real gates. Dashed outlines are neighbouring fences; switch on satellite imagery to check the shape against the site."
          actions={<Toolbar>
            <Toggle checked={showCrossings} onChange={setShowCrossings} label="Crossings" />
            <Toggle checked={showNeighbours} onChange={setShowNeighbours} label="Neighbours" />
          </Toolbar>}>
          <GeoMap height={460} onReady={setMap} />
        </Card>

        <Card title="Shape & trust" icon={Ruler}>
          <div className="grid grid-cols-2 gap-2.5">
            <Field label="State" value={f.s_state ? `${f.s_state}${f.d_state_offset_m != null ? ` (offshore, ${fmtMetres(f.d_state_offset_m)} away)` : ''}` : null} />
            <Field label="District" value={f.s_states ? `${f.s_district || '—'} · also in ${String(f.s_states).split(',').filter((s: string) => s !== f.s_state).join(', ')}` : f.s_district} />
            <Field label="Area" value={fmtArea(f.d_area_sqm)} />
            <Field label="Perimeter" value={fmtMetres(f.d_perimeter_m)} />
            <Field label="Vertices" value={fmtInt(f.i_vertices)} />
            <Field label="Deepest point inside" value={fmtMetres(f.d_inradius_m)} />
            <Field label="Detection band" value={`±${f.band_m} m`} />
            <Field label="Declared tolerance" value={f.i_tolerance ? `${f.i_tolerance} m` : 'none'} />
            <Field label="Speed limit" value={f.i_max_speed ? `${f.i_max_speed} km/h` : 'none'} />
            <Field label="Centroid" mono value={`${Number(f.d_centroid_lat).toFixed(5)}, ${Number(f.d_centroid_long).toFixed(5)}`} />
          </div>
          <div className="mt-4 space-y-2">
            {f.b_self_intersecting ? (
              <Note tone="warn" title="The ring crosses itself.">Which side is inside is ambiguous, so treat visits here as provisional.</Note>
            ) : null}
            {f.s_geom_notes && <Note tone="warn" title="Geometry note:">{f.s_geom_notes.replace(/_/g, ' ')}</Note>}
            {detail.data.sites_sharing_name > 1 && (
              <Note tone="warn" title={`${detail.data.sites_sharing_name} sites share this name.`}>
                Only the site id is unique; reports joining on the name would merge them.
              </Note>
            )}
            {detail.data.import_notes.map((n: any, i: number) => (
              <Note key={i} tone="warn" title={n.s_reason.replace(/_/g, ' ')}>{n.s_detail}</Note>
            ))}
            <p className="text-xs text-gray-500 pt-1">
              Band ±{f.band_m} m: a truck counts as inside only once {f.band_m} m past the boundary, and as outside once{' '}
              {f.band_m} m beyond it; in between it keeps its previous state. Scaled to this fence's size.
            </p>
            {detail.data.site && (
              <p className="text-xs text-gray-600">
                Created {fmtDateTime(detail.data.site.dt_created)}{detail.data.site.s_created_by ? ` by ${detail.data.site.s_created_by}` : ''}
                {detail.data.site.dt_modified ? ` · modified ${fmtDateTime(detail.data.site.dt_modified)}` : ''}
              </p>
            )}
          </div>
        </Card>
      </div>

      {(nesting.parents.length > 0 || nesting.children.length > 0) && (
        <Card title="Nesting" icon={Layers} className="mb-6"
          subtitle="Fences this one sits inside, and fences inside it. A truck here is inside all of the outer ones at the same time; the innermost is the one reported as where it was.">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
            <div>
              <p className="text-xs text-gray-400 mb-2">Inside ({nesting.parents.length})</p>
              <div className="flex flex-wrap gap-1.5">
                {nesting.parents.length === 0 && <span className="text-xs text-gray-600">nothing — outermost fence</span>}
                {nesting.parents.map((p: any) => (
                  <button key={p.site_id} onClick={() => navigate(`/geo/geofences/${p.site_id}`)}
                    className="px-2 py-1 rounded-md bg-gray-800 hover:bg-gray-700 text-xs text-gray-200">
                    {p.name} <span className="text-gray-500">· {p.scale}</span>
                  </button>
                ))}
              </div>
            </div>
            <div>
              <p className="text-xs text-gray-400 mb-2">Contains ({nesting.children_total ?? nesting.children.length})</p>
              <div className="flex flex-wrap gap-1.5 max-h-40 overflow-y-auto">
                {nesting.children.length === 0 && <span className="text-xs text-gray-600">nothing</span>}
                {nesting.children.map((c: any) => (
                  <button key={c.site_id} onClick={() => navigate(`/geo/geofences/${c.site_id}`)}
                    className="px-2 py-1 rounded-md bg-gray-800 hover:bg-gray-700 text-xs text-gray-200">
                    {c.name} <span className="text-gray-500">· {c.scale}</span>
                  </button>
                ))}
              </div>
            </div>
          </div>
        </Card>
      )}

      {activity.loading && !act && <Spinner label="Loading activity" />}
      {act && (
        <>
          <div className="grid grid-cols-1 xl:grid-cols-2 gap-6 mb-6">
            <Card title="Day by day" icon={CalendarDays}
              subtitle="Arrivals (observed entries) and distinct vehicles inside, per calendar day.">
              {act.daily.length ? (
                <SeriesChart data={act.daily} x="d_day" height={230} xFormat={v => fmtDayShort(v)}
                  series={[
                    { key: 'i_entries', label: 'Arrivals', color: PALETTE.blue },
                    { key: 'i_vehicles', label: 'Vehicles inside', color: PALETTE.pair, type: 'line' },
                  ]} />
              ) : <p className="text-sm text-gray-500 py-8 text-center">No visits in this run.</p>}
            </Card>
            <Card title="When trucks arrive" icon={Grid3x3}
              subtitle="Observed arrivals by weekday and hour. Shift patterns and gate queues show up here.">
              <WeekHourHeatmap matrix={act.weekday_hour} />
            </Card>
          </div>

          <div className="grid grid-cols-1 xl:grid-cols-3 gap-6 mb-6">
            <Card title="How long they stay" icon={BarChart3}
              subtitle={`${fmtInt(act.dwell.measured_visits)} fully observed visits. ${fmtInt(act.open_visits)} were still inside when the trail ended and ${fmtInt(act.unobserved_entries)} were already inside when it began — those are excluded here because their stay is only a lower bound.`}>
              <SeriesChart data={act.dwell_histogram} x="bucket" height={200}
                series={[{ key: 'visits', label: 'Visits', color: PALETTE.purple }]} />
              <div className="grid grid-cols-4 gap-2 mt-3">
                <Field label="25%" value={fmtDuration(act.dwell.p25_s)} />
                <Field label="Median" value={fmtDuration(act.dwell.p50_s)} />
                <Field label="75%" value={fmtDuration(act.dwell.p75_s)} />
                <Field label="90%" value={fmtDuration(act.dwell.p90_s)} />
              </div>
            </Card>
            <Card title="Transporters" icon={Building2} subtitle="Visits by carrier, with each carrier's median stay.">
              <BarList rows={act.top_transporters.map((t: any) => ({
                key: t.transporter, label: t.transporter, value: t.visits, sub: fmtDuration(t.dwell_p50_s),
                onClick: () => navigate(`/geo/transporters/detail?name=${encodeURIComponent(t.transporter)}`),
              }))} />
            </Card>
            <Card title="Most frequent vehicles" icon={Users}>
              <BarList rows={act.top_vehicles.map((v: any) => ({
                key: v.s_asset_id, label: v.s_asset_id, value: v.visits,
                onClick: () => navigate(`/geo/vehicles/${encodeURIComponent(v.s_asset_id)}`),
              }))} />
              {act.violations.length > 0 && (
                <div className="mt-4 pt-3 border-t border-gray-800 space-y-1">
                  {act.violations.map((v: any) => (
                    <p key={v.s_kind} className="text-xs text-gray-400 flex justify-between">
                      <span className="text-red-400">{KIND_LABEL[v.s_kind] || v.s_kind}</span><span>{fmtInt(v.n)}</span>
                    </p>
                  ))}
                </div>
              )}
            </Card>
          </div>
        </>
      )}

      <VisitsTable siteId={siteId} />
    </div>
  );
}

function VisitsTable({ siteId }: { siteId: string }) {
  const navigate = useNavigate();
  const [q, setQ] = useState('');
  const [from, setFrom] = useState('');
  const [to, setTo] = useState('');
  const [page, setPage] = useState(1);
  const { sort, order, onSort } = useSort('enter');
  const visits = useApi(() => api.geofenceVisits(siteId, { q, from, to, sort, order, page, page_size: 25 }),
    [siteId, q, from, to, sort, order, page]);
  const rows = useMemo(() => visits.data?.items ?? [], [visits.data]);

  return (
    <Card title="Every visit" icon={MapPinned}
      subtitle="Each row is one confirmed stay inside this fence. ± shows the GPS gap around an entry or exit — the true crossing lies somewhere inside it."
      actions={<Toolbar>
        <SearchInput value={q} onChange={v => { setQ(v); setPage(1); }} placeholder="Vehicle, driver, transporter, trip" />
        <DateInput label="From" value={from} onChange={v => { setFrom(v); setPage(1); }} />
        <DateInput label="To" value={to} onChange={v => { setTo(v); setPage(1); }} />
      </Toolbar>}>
      {visits.error ? <ErrorBox error={visits.error} /> : (
        <>
          <DataTable rows={rows} loading={visits.loading} sort={sort} order={order} onSort={k => { onSort(k); setPage(1); }}
            onRowClick={r => navigate(`/geo/trips/${r.i_trip_no}`)} rowKey={r => r.id}
            columns={[
              { key: 'vehicle', label: 'Vehicle', sortable: true, render: r => <span className="text-gray-100">{r.s_asset_id}</span> },
              { key: 'trip', label: 'Trip', sortable: true, render: r => <TripRef trip={r.i_trip_no} trips={r.s_trips} /> },
              { key: 'enter', label: 'Entered', sortable: true, render: r => (
                <span>{r.b_entry_observed ? fmtDateTime(r.dt_enter) : <span className="text-gray-500" title="Already inside when the trail began">≤ {fmtDateTime(r.dt_enter)}</span>}
                  {r.i_enter_gap_seconds > 600 && <span className="ml-1 text-xs text-amber-400">±{fmtDuration(r.i_enter_gap_seconds)}</span>}</span>
              ) },
              { key: 'exit', label: 'Left', sortable: true, render: r => (
                r.b_open ? <span className="text-amber-400" title="Still inside when the trail ended">still inside</span> :
                  <span>{fmtDateTime(r.dt_exit)}{r.i_exit_gap_seconds > 600 && <span className="ml-1 text-xs text-amber-400">±{fmtDuration(r.i_exit_gap_seconds)}</span>}</span>
              ) },
              { key: 'dwell', label: 'Stay', sortable: true, align: 'right', render: r => `${fmtDuration(r.i_dwell_seconds)}${r.b_open ? '+' : ''}` },
              { key: 'confirmed', label: 'Confirmed by', render: r => <Badge variant={r.s_confirmed_by === 'escape' ? 'purple' : 'neutral'}>{r.s_confirmed_by}</Badge> },
              { key: 'transporter', label: 'Transporter', render: r => <span className="text-xs text-gray-400">{r.s_trans_name || '—'}</span> },
              { key: 'lane', label: 'Lane', render: r => <span className="text-xs text-gray-500">{r.s_origin ? `${r.s_origin} → ${r.s_destination}` : '—'}</span> },
            ]} />
          <Pagination page={page} pages={visits.data?.pages ?? 1} total={visits.data?.total} onPage={setPage} />
        </>
      )}
    </Card>
  );
}
