import { useEffect, useMemo, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import L from 'leaflet';
import {
  AlertTriangle, ArrowDown, CircleDot, Clock, Crosshair, EyeOff, Factory, Hexagon, ListTree, MapPinned, Route,
  SatelliteDish, ShieldCheck, Timer, Truck,
} from 'lucide-react';
import { api } from '../lib/api';
import { useApi } from '../hooks/useApi';
import {
  Badge, Card, ConfidenceBadge, DataTable, EntityLink, ErrorBox, Field, KPI, Note, PageHeader, QualityBadge,
  ScaleBadge, Segmented, Spinner, Toggle, Toolbar,
} from '../components/ui';
import GeoMap from '../components/map/GeoMap';
import { dot, escapeHtml, fencePolygon } from '../components/map/layers';
import { KPIGrid } from '../components/drill';
import TripReport from '../components/trip/TripReport';
import RouteCard from '../components/routes/RouteCard';
import { PALETTE } from '../components/charts';
import {
  KIND_LABEL, QUALITY_TEXT, fmtDateTime, fmtDateTimeFull, fmtDuration, fmtInt, fmtKm, fmtMetres,
} from '../lib/format';

export default function TripDetail() {
  const { tripNo = '' } = useParams();
  const navigate = useNavigate();
  const trip = useApi(() => api.trip(tripNo), [tripNo]);
  const track = useApi(() => api.tripTrack(tripNo), [tripNo]);
  const [map, setMap] = useState<L.Map | null>(null);
  const [layers, setLayers] = useState({ raw: true, fitted: true, fences: true, stops: true, gaps: true, spikes: true });
  const [scope, setScope] = useState<'facility' | 'all'>('facility');
  const set = (k: keyof typeof layers) => (v: boolean) => setLayers(s => ({ ...s, [k]: v }));

  useEffect(() => {
    if (!map || !track.data) return;
    const t = track.data;
    const group = L.layerGroup().addTo(map);
    const pts: any[] = t.points;

    if (layers.fences) {
      for (const f of t.fences) {
        if (!f.ring?.length || f.scale === 'regional') continue;
        fencePolygon(f, { onClick: () => navigate(`/geo/geofences/${f.site_id}`) }).addTo(group);
      }
    }
    if (layers.raw) {
      L.polyline(pts.map(p => [p[1], p[2]]), { color: PALETTE.rawTrack, weight: 1, opacity: 0.55 }).addTo(group);
    }
    if (layers.fitted && t.fitted) {
      L.polyline(pts.filter(p => p[4] != null).map(p => [p[4], p[5]]), { color: PALETTE.blue, weight: 2.5, opacity: 0.9 })
        .addTo(group);
    }
    if (layers.gaps) {
      for (const g of t.gap_routes) {
        L.polyline(g.path, {
          color: g.routed ? PALETTE.purple : PALETTE.amber, weight: 2, dashArray: '6 6', opacity: 0.9,
        }).bindTooltip(`GPS silent ${fmtDuration(g.gap_s)}${g.routed ? ' · road path from OSRM' : ' · straight line (no OSRM)'}` +
          (g.unexplained_s ? ` · ${fmtDuration(g.unexplained_s)} unexplained` : ''), { className: 'geo-tip', sticky: true })
          .addTo(group);
      }
    }
    if (layers.spikes) {
      for (const p of pts) {
        if (p[7] === 'spike') {
          L.polyline([[p[1], p[2]], [p[4], p[5]]], { color: PALETTE.amber, weight: 1, dashArray: '2 3' }).addTo(group);
          dot(p[1], p[2], PALETTE.amber, 4, `Spike at ${escapeHtml(p[0])} · corrected ${fmtMetres(p[8])}`).addTo(group);
        } else if (p[6] === 'reject') {
          dot(p[1], p[2], PALETTE.red, 3, `Refused (${escapeHtml(p[9] || '')}) at ${escapeHtml(p[0])}`).addTo(group);
        }
      }
    }
    if (layers.stops && trip.data) {
      for (const s of trip.data.stops) {
        const outside = s.i_site_id == null;
        L.circleMarker([Number(s.d_lat), Number(s.d_long)], {
          radius: Math.min(14, 4 + Math.sqrt(s.i_duration_s / 600)),
          color: outside ? PALETTE.amber : PALETTE.green, weight: 1.5, fillOpacity: 0.15,
        }).bindTooltip(`${outside ? 'Stop outside any fence' : `Stop at ${escapeHtml(s.s_site_name)}`} · ${fmtDuration(s.i_duration_s)}` +
          ` · from ${fmtDateTime(s.dt_start)}`, { className: 'geo-tip' }).addTo(group);
      }
    }
    return () => { group.remove(); };
  }, [map, track.data, trip.data, layers, navigate]);

  useEffect(() => {
    if (!map || !track.data?.bbox) return;
    const [x0, y0, x1, y1] = track.data.bbox;
    map.fitBounds([[y0, x0], [y1, x1]], { padding: [30, 30], maxZoom: 15 });
  }, [map, track.data?.trip_no]); // eslint-disable-line react-hooks/exhaustive-deps

  const visits = useMemo(() => (trip.data?.visits ?? []).filter((v: any) =>
    scope === 'all' || v.s_scale !== 'regional'), [trip.data, scope]);

  if (trip.loading && !trip.data) return <Spinner label={`Loading trip ${tripNo}`} />;
  if (trip.error) return <ErrorBox error={trip.error} onRetry={trip.reload} />;
  if (!trip.data) return null;

  const d = trip.data;
  const t = d.trip;
  const panTo = (lat: number, lon: number) => map?.setView([lat, lon], Math.max(map.getZoom(), 15));
  // A moment of the trip on the map: the fix nearest that time.
  const focusAt = (ms: number) => {
    const pts: any[] = track.data?.points ?? [];
    let best: any = null;
    let bestDt = Infinity;
    for (const p of pts) {
      const dt = Math.abs(new Date(String(p[0]).replace(' ', 'T')).getTime() - ms);
      if (dt < bestDt) { bestDt = dt; best = p; }
    }
    if (best) panTo(best[4] ?? best[1], best[5] ?? best[2]);
  };
  const small = (rows: any[], cols: any[], empty: string) => () => (
    <div className="max-h-[360px] overflow-y-auto"><DataTable dense rows={rows} columns={cols} empty={empty} /></div>
  );
  const visitCols = [
    { key: 's_site_name', label: 'Fence', render: (r: any) => <span className="text-gray-100 text-xs">{r.s_site_name}</span> },
    { key: 's_scale', label: 'Scale', render: (r: any) => <ScaleBadge scale={r.s_scale} /> },
    { key: 'dt_enter', label: 'Entered', render: (r: any) => <span className="text-xs">{fmtDateTime(r.dt_enter)}</span> },
    { key: 'i_dwell_seconds', label: 'Stay', align: 'right' as const, render: (r: any) => `${fmtDuration(r.i_dwell_seconds)}${r.b_open ? '+' : ''}` },
  ];
  const gapCols = [
    { key: 'dt_from', label: 'GPS silent from', render: (r: any) => <span className="text-xs">{fmtDateTime(r.dt_from)}</span> },
    { key: 'i_gap_s', label: 'For', align: 'right' as const, render: (r: any) => fmtDuration(r.i_gap_s) },
    { key: 'd_straight_m', label: 'Moved', align: 'right' as const, render: (r: any) => fmtMetres(r.d_straight_m) },
    { key: 's_route', label: 'Road path', render: (r: any) => r.s_route === 'ok' ? fmtMetres(r.d_route_m) : <span className="text-gray-500">{r.s_route}</span> },
  ];
  const movingGaps = d.gaps.filter((g: any) => g.s_kind === 'moving');

  return (
    <div className="animate-fade-in">
      <PageHeader
        back={{ to: '/geo/trips', label: 'All trips' }}
        title={<>Trip {t.i_trip_no}</>}
        badges={<>
          <QualityBadge quality={t.s_quality} reason={t.s_quality_reason} />
          {t.s_status && <Badge variant="info">{t.s_status}</Badge>}
          {t.s_trip_class && <Badge>{t.s_trip_class}</Badge>}
        </>}
        subtitle={<>
          <EntityLink to={`/geo/vehicles/${encodeURIComponent(t.s_asset_id)}`}>{t.s_asset_id}</EntityLink>
          {t.s_trans_name && <> · <EntityLink to={`/geo/transporters/detail?name=${encodeURIComponent(t.s_trans_name)}`}>{t.s_trans_name}</EntityLink></>}
          {t.s_driver_name && <> · {t.s_driver_name}</>}
          {t.s_origin && <> · <EntityLink to={`/geo/lanes/detail?origin=${encodeURIComponent(t.s_origin)}&destination=${encodeURIComponent(t.s_destination || '?')}`}>{t.s_origin} → {t.s_destination}</EntityLink></>}
        </>}
      />

      <KPIGrid className="grid grid-cols-2 md:grid-cols-4 2xl:grid-cols-8 gap-3 mb-6">
        <KPI label="Places visited" value={fmtInt(t.i_places)} icon={Factory} color="blue" hint={`${fmtInt(t.i_facility_visits)} fence visits`}
          details={small(d.places, [
            { key: 'name', label: 'Place', render: (r: any) => <span className="text-gray-100 text-xs">{r.name}</span> },
            { key: 'innermost', label: 'Innermost fence', render: (r: any) => <span className="text-xs text-gray-400">{r.innermost}</span> },
            { key: 'enter', label: 'Arrived', render: (r: any) => <span className="text-xs">{fmtDateTime(r.enter)}</span> },
            { key: 'exit', label: 'Left', render: (r: any) => r.open ? <span className="text-amber-400 text-xs">still inside</span> : <span className="text-xs">{fmtDateTime(r.exit)}</span> },
            { key: 'dwell_s', label: 'Stay', align: 'right' as const, render: (r: any) => fmtDuration(r.dwell_s) },
          ], 'No places')} />
        <KPI label="Time at facilities" value={fmtDuration(t.i_facility_dwell_s)} icon={Clock} color="purple"
          details={small(d.visits.filter((v: any) => v.b_primary && v.s_scale !== 'regional'), visitCols, 'No facility visits')} />
        <KPI label="Transit" value={fmtDuration(t.i_transit_s)} icon={Route} color="cyan" hint="first exit → last entry"
          details={small(d.timeline.filter((x: any) => x.kind === 'travel'), [
            { key: 'from', label: 'From', render: (r: any) => <span className="text-xs">{r.from}</span> },
            { key: 'to', label: 'To', render: (r: any) => <span className="text-xs">{r.to}</span> },
            { key: 'travel_s', label: 'Travel', align: 'right' as const, render: (r: any) => fmtDuration(r.travel_s) },
            { key: 'stops_outside', label: 'Stops', align: 'right' as const, render: (r: any) => r.stops_outside ? `${r.stops_outside} · ${fmtDuration(r.stopped_s)}` : '0' },
            { key: 'moving_gaps', label: 'GPS silent', align: 'right' as const, render: (r: any) => r.moving_gaps ? fmtDuration(r.unobserved_s) : '—' },
          ], 'Fewer than two places: no transit')} />
        <KPI label="Distance (fitted)" value={fmtKm(t.d_distance_km)} icon={Truck} color="green"
          hint={t.d_gap_distance_km ? `+${fmtKm(t.d_gap_distance_km)} unobserved` : undefined}
          details={small(movingGaps, gapCols, 'The whole distance was seen by the GPS')} />
        <KPI label="Alerts" value={fmtInt(t.i_violations)} icon={AlertTriangle} color={t.i_violations ? 'red' : 'gray'}
          details={small(d.violations, [
            { key: 'dt_event', label: 'When', render: (r: any) => <span className="text-xs">{fmtDateTime(r.dt_event)}</span> },
            { key: 's_kind', label: 'What', render: (r: any) => <span className="text-red-400 text-xs">{KIND_LABEL[r.s_kind] || r.s_kind}</span> },
            { key: 's_site_name', label: 'Where', render: (r: any) => <span className="text-xs">{r.s_site_name}</span> },
            { key: 's_detail', label: 'Detail', render: (r: any) => <span className="text-xs text-gray-400">{r.s_detail}</span> },
          ], 'No alerts')} />
        <KPI label="GPS fixes used" value={fmtInt(t.i_pings_used)} icon={SatelliteDish} color="blue" hint={`of ${fmtInt(t.i_pings_read)} read`}
          details={small(d.fit_mix, [
            { key: 's_fit', label: 'Method', render: (r: any) => r.s_role === 'reject' ? <span className="text-red-400">refused</span> : (r.s_fit || '—') },
            { key: 's_role', label: 'Role' },
            { key: 'n', label: 'Fixes', align: 'right' as const, render: (r: any) => fmtInt(r.n) },
            { key: 'max_shift_m', label: 'Max move', align: 'right' as const, render: (r: any) => fmtMetres(r.max_shift_m) },
          ], 'No fit recorded')} />
        <KPI label="Spikes corrected" value={fmtInt(t.i_spikes)} icon={Crosshair} color="amber"
          details={() => <p className="text-sm text-gray-400">{fmtInt(t.i_spikes)} fixes jumped away and back and were pulled back to where the truck was: the amber dots on the map, each with a line to where the fit put it.</p>} />
        <KPI label="Moved while silent" value={fmtInt(t.i_moving_gaps)} icon={EyeOff} color={t.i_moving_gaps ? 'amber' : 'gray'}
          hint={t.i_moving_gap_s ? fmtDuration(t.i_moving_gap_s) : undefined}
          details={small(movingGaps, gapCols, 'No silent movement')} />
      </KPIGrid>

      <TripReport tripNo={tripNo} track={track.data} onFocus={focusAt} />
      <RouteCard tripNo={tripNo} />

      {t.i_siblings > 0 && (
        <div className="mb-6">
          <Note title={`Shares its truck with ${t.i_siblings} other consignment trip${t.i_siblings > 1 ? 's' : ''}.`}>
            The fleet system opens one trip per consignment, so trip{t.i_siblings > 1 ? 's' : ''}{' '}
            {String(t.s_sibling_trips || '').split(',').filter(Boolean).map((x: string, i: number) => (
              <span key={x}>{i > 0 && ', '}<EntityLink to={`/geo/trips/${x}`}>{x}</EntityLink></span>
            ))}{' '}
            carried the same GPS fixes over part of this trip. Everything on this page is this consignment's own view;
            fence, day, vehicle and transporter totals count a shared stay, alert or kilometre once.
          </Note>
        </div>
      )}

      {t.s_quality !== 'good' && (
        <div className="mb-6">
          <Note tone="warn" title={`GPS quality: ${t.s_quality}.`}>
            {QUALITY_TEXT[t.s_quality] || ''}{t.s_quality_reason ? ` — ${t.s_quality_reason}.` : '.'} A fence this trip
            appears not to have visited may simply have been passed while the tracker was silent.
          </Note>
        </div>
      )}

      <div className="grid grid-cols-1 xl:grid-cols-3 gap-6 mb-6">
        <Card title="Route on the map" icon={MapPinned} className="xl:col-span-2"
          subtitle="Grey: raw GPS. Blue: fitted positions the geofences were decided on. Amber dots: spikes, with a line to where the fit put them. Dashed: stretches the tracker was silent. Circles: stops — green inside a facility, amber outside every fence."
          actions={<Toolbar>
            <Toggle checked={layers.raw} onChange={set('raw')} label="Raw" />
            <Toggle checked={layers.fitted} onChange={set('fitted')} label="Fitted" />
            <Toggle checked={layers.fences} onChange={set('fences')} label="Fences" />
            <Toggle checked={layers.stops} onChange={set('stops')} label="Stops" />
            <Toggle checked={layers.spikes} onChange={set('spikes')} label="Spikes" />
            <Toggle checked={layers.gaps} onChange={set('gaps')} label="Gaps" />
          </Toolbar>}>
          {track.error ? <ErrorBox error={track.error} /> : <GeoMap height={520} onReady={setMap} />}
          {track.data && track.data.returned < track.data.total_points && (
            <p className="text-xs text-gray-500 mt-2">
              Drawing {fmtInt(track.data.returned)} of {fmtInt(track.data.total_points)} fixes; every spike, refused fix and stop boundary is kept.
            </p>
          )}
        </Card>

        <Card title="Where the truck was" icon={ListTree}
          subtitle="Places in order. Nested fences are folded into one place, named by its outermost facility fence.">
          {d.timeline.length === 0 ? (
            <p className="text-sm text-gray-500 py-6 text-center">No facility fence was visited on this trip.</p>
          ) : (
            <div className="space-y-2 max-h-[520px] overflow-y-auto pr-1">
              {d.timeline.map((item: any, i: number) => item.kind === 'place' ? (
                <button key={i} onClick={() => {
                  const v = d.visits.find((x: any) => x.i_site_id === item.innermost_site_id);
                  const st = d.stops.find((s: any) => s.i_site_id != null && s.dt_start >= item.enter);
                  if (st) panTo(Number(st.d_lat), Number(st.d_long));
                  else if (v) navigate(`/geo/geofences/${item.site_id}`);
                }}
                  className="w-full text-left rounded-lg border border-gray-800 bg-gray-800/40 hover:bg-gray-800 p-3">
                  <div className="flex items-start justify-between gap-2">
                    <p className="text-sm text-gray-100 font-medium">{item.name}</p>
                    <ScaleBadge scale={item.scale} />
                  </div>
                  {item.innermost !== item.name && <p className="text-xs text-gray-500 mt-0.5">at {item.innermost}</p>}
                  <div className="flex items-center gap-3 text-xs text-gray-400 mt-1.5 flex-wrap">
                    <span>{item.entry_observed ? fmtDateTime(item.enter) : <span title="Already inside when GPS began">≤ {fmtDateTime(item.enter)}</span>}</span>
                    <span>→ {item.open ? <span className="text-amber-400">still inside</span> : fmtDateTime(item.exit)}</span>
                    <span className="text-gray-200 font-medium">{fmtDuration(item.dwell_s)}{item.open ? '+' : ''}</span>
                  </div>
                  {item.zones.length > 1 && (
                    <p className="text-xs text-gray-600 mt-1 truncate" title={item.zones.join(', ')}>{item.zones.length} zones: {item.zones.join(', ')}</p>
                  )}
                </button>
              ) : (
                <div key={i} className="flex items-center gap-2 pl-3 text-xs text-gray-500">
                  <ArrowDown className="w-3.5 h-3.5 shrink-0" />
                  <span>{fmtDuration(item.travel_s)} to next place</span>
                  {item.stops_outside > 0 && <span className="text-amber-400">· {item.stops_outside} stop{item.stops_outside > 1 ? 's' : ''} ({fmtDuration(item.stopped_s)})</span>}
                  {item.moving_gaps > 0 && <span className="text-purple-300">· silent {fmtDuration(item.unobserved_s)}</span>}
                </div>
              ))}
            </div>
          )}
        </Card>
      </div>

      {d.tms && (
        <Card title="Fleet-system stamps against the geofences" icon={ShieldCheck} className="mb-6"
          subtitle="The fleet system's own times for this trip, checked against what the GPS and the fences show. A truck that physically leaves hours after its gate-out stamp was held at the plant for those hours.">
          <div className="grid grid-cols-2 md:grid-cols-3 2xl:grid-cols-6 gap-3">
            <Field label="Booking / plant entry (fleet system)" value={fmtDateTime(d.tms.booking)} />
            <Field label={`Entered ${d.tms.first_place || 'first place'} (geofence)`} value={fmtDateTime(d.tms.first_enter)} />
            <Field label="Gate-out stamp (fleet system)" value={fmtDateTime(d.tms.gate_out)} />
            <Field label="Actually left (geofence)" value={fmtDateTime(d.tms.first_exit)} />
            <Field label="Left after gate-out by" value={
              d.tms.exit_after_gate_out_s == null ? '—' :
                <span className={d.tms.exit_after_gate_out_s > 3600 ? 'text-amber-400 font-medium' : ''}>{fmtDuration(d.tms.exit_after_gate_out_s)}</span>
            } />
            <Field label={`Arrived ${d.tms.last_place || 'destination'} vs ATA`} value={
              d.tms.arrival_vs_ata_s == null ? '—' : `${fmtDuration(d.tms.arrival_vs_ata_s)} ${d.tms.arrival_vs_ata_s >= 0 ? 'after' : 'before'} ATA`
            } />
          </div>
        </Card>
      )}

      <Card title="Every geofence crossed" icon={Hexagon} className="mb-6"
        subtitle="Each confirmed stay inside a fence, nested ones included. Confirmed by: dwell = the new state held long enough; escape = the truck got far enough past the boundary that brevity did not matter. ± is the GPS gap around the crossing."
        actions={<Segmented value={scope} onChange={setScope}
          options={[{ value: 'facility', label: 'Facilities' }, { value: 'all', label: 'Include regional' }]} />}>
        <DataTable dense rows={visits} rowKey={r => r.id}
          onRowClick={r => navigate(`/geo/geofences/${r.i_site_id}`)}
          empty="No fence crossings on this trip"
          columns={[
            { key: 's_site_name', label: 'Fence', render: r => (
              <div><p className={r.b_primary ? 'text-gray-100' : 'text-gray-400'}>{r.s_site_name}</p>
                {!r.b_primary && <p className="text-xs text-gray-600">outer zone</p>}</div>
            ) },
            { key: 's_scale', label: 'Scale', render: r => <ScaleBadge scale={r.s_scale} /> },
            { key: 'dt_enter', label: 'Entered', render: r => (
              <span title={fmtDateTimeFull(r.dt_enter)}>{r.b_entry_observed ? fmtDateTime(r.dt_enter) : `≤ ${fmtDateTime(r.dt_enter)}`}
                {r.i_enter_gap_seconds > 600 && <span className="ml-1 text-xs text-amber-400">±{fmtDuration(r.i_enter_gap_seconds)}</span>}</span>
            ) },
            { key: 'dt_exit', label: 'Left', render: r => r.b_open ? <span className="text-amber-400">still inside</span> : (
              <span title={fmtDateTimeFull(r.dt_exit)}>{fmtDateTime(r.dt_exit)}
                {r.i_exit_gap_seconds > 600 && <span className="ml-1 text-xs text-amber-400">±{fmtDuration(r.i_exit_gap_seconds)}</span>}</span>
            ) },
            { key: 'i_dwell_seconds', label: 'Stay', align: 'right', render: r => `${fmtDuration(r.i_dwell_seconds)}${r.b_open ? '+' : ''}` },
            { key: 'i_pings', label: 'Fixes', align: 'right', render: r => fmtInt(r.i_pings) },
            { key: 'i_max_speed', label: 'Max km/h', align: 'right', render: r => r.i_max_speed ?? '—' },
            { key: 's_confirmed_by', label: 'Confirmed by', render: r => <Badge variant={r.s_confirmed_by === 'escape' ? 'purple' : 'neutral'}>{r.s_confirmed_by}</Badge> },
          ]} />
      </Card>

      <div className="grid grid-cols-1 xl:grid-cols-2 gap-6 mb-6">
        <Card title={`Alerts (${d.violations.length})`} icon={AlertTriangle} iconClass="text-red-400">
          <DataTable dense rows={d.violations} empty="No alerts on this trip"
            onRowClick={r => panTo(Number(r.d_lat), Number(r.d_long))}
            columns={[
              { key: 'dt_event', label: 'When', render: r => fmtDateTime(r.dt_event) },
              { key: 's_kind', label: 'What', render: r => <span className="text-red-400">{KIND_LABEL[r.s_kind] || r.s_kind}</span> },
              { key: 's_site_name', label: 'Where', render: r => <EntityLink to={`/geo/geofences/${r.i_site_id}`}>{r.s_site_name}</EntityLink> },
              { key: 's_detail', label: 'Detail', render: r => <span className="text-xs text-gray-400">{r.s_detail}</span> },
            ]} />
        </Card>
        <Card title={`Stops (${d.stops.length})`} icon={CircleDot}
          subtitle="Standstills of three minutes or more. Click one to see it on the map.">
          <div className="max-h-[320px] overflow-y-auto">
            <DataTable dense rows={d.stops} onRowClick={r => panTo(Number(r.d_lat), Number(r.d_long))}
              columns={[
                { key: 'dt_start', label: 'From', render: r => fmtDateTime(r.dt_start) },
                { key: 'i_duration_s', label: 'For', align: 'right', render: r => fmtDuration(r.i_duration_s) },
                { key: 's_site_name', label: 'At', render: r => r.s_site_name
                  ? <span className="text-gray-200">{r.s_site_name}</span> : <span className="text-amber-400">outside any fence</span> },
                { key: 'd_p90_spread_m', label: 'GPS scatter', align: 'right', title: '90% of raw fixes within', render: r => fmtMetres(r.d_p90_spread_m) },
              ]} />
          </div>
        </Card>
      </div>

      {d.inferred.length > 0 && (
        <Card title="Fences passed while the tracker was silent" icon={EyeOff} iconClass="text-purple-300" className="mb-6"
          subtitle="Inferred from the road path across a GPS hole — never counted as visits. The window is when it must have happened; the estimate assumes steady driving along the route.">
          <DataTable dense rows={d.inferred}
            onRowClick={r => navigate(`/geo/geofences/${r.i_site_id}`)}
            columns={[
              { key: 's_site_name', label: 'Fence', render: r => <span className="text-gray-100">{r.s_site_name}</span> },
              { key: 's_kind', label: 'How', render: r => r.s_kind === 'through' ? 'route passes through' : 'route passes near, truck stopped somewhere' },
              { key: 'window', label: 'Window', render: r => `${fmtDateTime(r.dt_gap_from)} → ${fmtDateTime(r.dt_gap_to)}` },
              { key: 'dt_est_enter', label: 'Estimated', render: r => r.dt_est_enter ? fmtDateTime(r.dt_est_enter) : '—' },
              { key: 's_confidence', label: 'Confidence', render: r => <ConfidenceBadge level={r.s_confidence} /> },
            ]} />
        </Card>
      )}

      <Card title="How this trip's GPS was treated" icon={ShieldCheck} iconClass="text-emerald-400"
        subtitle="What the filter-and-fit stage did before any fence was checked. Raw fixes are kept unchanged; these are the positions the decisions above rest on.">
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          <div>
            <p className="text-xs text-gray-400 mb-2">Fit applied to each fix</p>
            <DataTable dense rows={d.fit_mix}
              columns={[
                { key: 's_fit', label: 'Method', render: r => r.s_role === 'reject' ? <span className="text-red-400">refused</span> : (r.s_fit || '—') },
                { key: 's_role', label: 'Role' },
                { key: 'n', label: 'Fixes', align: 'right', render: r => fmtInt(r.n) },
                { key: 'max_shift_m', label: 'Max move', align: 'right', render: r => fmtMetres(r.max_shift_m) },
              ]} />
          </div>
          <div>
            <p className="text-xs text-gray-400 mb-2">Refused before fitting</p>
            <DataTable dense rows={d.rejects} empty="Nothing refused"
              columns={[
                { key: 's_reason', label: 'Reason', render: r => r.s_reason.replace(/_/g, ' ') },
                { key: 'i_count', label: 'Fixes', align: 'right', render: r => fmtInt(r.i_count) },
              ]} />
          </div>
          <div>
            <p className="text-xs text-gray-400 mb-2">GPS holes of 5 minutes or more</p>
            <div className="max-h-[240px] overflow-y-auto">
              <DataTable dense rows={d.gaps} empty="No holes"
                columns={[
                  { key: 'dt_from', label: 'From', render: r => fmtDateTime(r.dt_from) },
                  { key: 'i_gap_s', label: 'Silent', align: 'right', render: r => fmtDuration(r.i_gap_s) },
                  { key: 's_kind', label: 'Kind', render: r => <Badge variant={r.s_kind === 'moving' ? 'warning' : 'neutral'}>{r.s_kind}</Badge> },
                  { key: 'd_straight_m', label: 'Moved', align: 'right', render: r => fmtMetres(r.d_straight_m) },
                ]} />
            </div>
          </div>
        </div>
        <p className="text-xs text-gray-600 mt-4">
          Run #{d.run_id} · {d.variant} positions · OSRM {d.osrm || 'off'} · <Timer className="inline w-3 h-3" /> GPS from{' '}
          {fmtDateTime(t.dt_first_ping)} to {fmtDateTime(t.dt_last_ping)}
        </p>
      </Card>
    </div>
  );
}
