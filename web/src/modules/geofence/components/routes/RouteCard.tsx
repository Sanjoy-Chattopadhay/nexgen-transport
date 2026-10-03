/**
 * One trip against its planned route: both paths on the map, every
 * deviation coloured by kind, and what the difference cost.
 */
import { useEffect, useState } from 'react';
import L from 'leaflet';
import { Navigation } from 'lucide-react';
import { api } from '../../lib/api';
import { useApi } from '../../hooks/useApi';
import { Badge, Card, DataTable, Field, Note } from '../ui';
import GeoMap from '../map/GeoMap';
import { dot } from '../map/layers';
import { PALETTE } from '../../lib/theme';
import { DeviationBadge, DEVIATION_LABEL, ExtraKm, Inr, fmtInr } from '../drill';
import { fmtDateTime, fmtDuration, fmtKm, fmtMetres, fmtPct } from '../../lib/format';

export function deviationColor(kind: string): string {
  return ({ detour: PALETTE.amber, shortcut: PALETTE.green, off_route_stop: PALETTE.purple, excursion: PALETTE.cyan,
    backtrack: PALETTE.red, alternate_route: PALETTE.pairAlt, reroute: PALETTE.cyan } as Record<string, string>)[kind] || PALETTE.amber;
}

export default function RouteCard({ tripNo }: { tripNo: string }) {
  const r = useApi(() => api.routeTrip(tripNo).catch(e => ({ error: e.message })), [tripNo]);
  const [map, setMap] = useState<L.Map | null>(null);
  const d: any = r.data;

  useEffect(() => {
    if (!map || !d || d.error) return;
    const group = L.layerGroup().addTo(map);
    const plan: [number, number][] = d.plan_path || [];
    const actual: any[] = d.actual_path || [];
    if (plan.length) L.polyline(plan, { color: PALETTE.legend, weight: 7, opacity: 0.25 }).bindTooltip('Planned route', { className: 'geo-tip', sticky: true }).addTo(group);
    // the driven path, split where a deviation starts and ends
    let run: [number, number][] = [];
    let cur = -1;
    const kindOf = (seq: number) => d.deviations.find((x: any) => x.i_seq === seq)?.s_kind;
    const flush = () => {
      if (run.length > 1) {
        const color = cur > 0 ? deviationColor(kindOf(cur)) : PALETTE.blue;
        const line = L.polyline(run, { color, weight: cur > 0 ? 3.5 : 2.5, opacity: 0.95 });
        if (cur > 0) line.bindTooltip(`${DEVIATION_LABEL[kindOf(cur)] || 'Deviation'} #${cur}`, { className: 'geo-tip', sticky: true });
        line.addTo(group);
      }
    };
    for (const p of actual) {
      if (p[2] !== cur) { flush(); run = run.length ? [run[run.length - 1]] : []; cur = p[2]; }
      run.push([p[0], p[1]]);
    }
    flush();
    // the plan last, thin and dashed, so it shows over the path it shares
    if (plan.length) L.polyline(plan, { color: PALETTE.tooltipText, weight: 1.5, opacity: 0.9, dashArray: '5 6' }).addTo(group);
    for (const dv of d.deviations) {
      dot(Number(dv.d_leave_lat), Number(dv.d_leave_long), deviationColor(dv.s_kind), 5,
        `${DEVIATION_LABEL[dv.s_kind]} #${dv.i_seq} · left the route ${fmtDateTime(dv.dt_leave)} · ${fmtDuration(dv.i_duration_s)} · furthest ${fmtMetres(dv.d_max_offset_m)}`).addTo(group);
    }
    const all = [...plan, ...actual.map(p => [p[0], p[1]] as [number, number])];
    if (all.length) map.fitBounds(L.latLngBounds(all), { padding: [24, 24], maxZoom: 14 });
    return () => { group.remove(); };
  }, [map, d]);

  if (!d) return null;
  if (d.error) {
    return (
      <Card title="Route against plan" icon={Navigation} className="mb-6">
        <Note>{String(d.error).includes('no loading') ? 'This trip has no loading and unloading place to plan a route between.' : d.error}</Note>
      </Card>
    );
  }
  const rt = d.route;
  const mode = rt.s_mode === 'osrm' ? 'OSRM road route' : 'the lane’s learned route';
  if (rt.s_status !== 'ok') {
    const why: Record<string, string> = {
      local: `a local move of ${fmtKm(rt.d_planned_km)} inside the works, not a journey: its path is not judged or priced`,
      short_history: 'its lane has too few trips yet to learn a route from (three are needed); set OSRM_URL to plan by road instead',
      no_route: 'OSRM found no route between its loading and unloading points',
      no_trail: 'no fitted trail was stored for it',
    };
    return (
      <Card title="Route against plan" icon={Navigation} className="mb-6">
        <Note>Not measured against a plan: {why[rt.s_status] || rt.s_status}.</Note>
      </Card>
    );
  }
  return (
    <Card title="Route against plan" icon={Navigation} className="mb-6"
      subtitle={`The transit, loading exit to unloading arrival, against ${mode}${rt.s_mode === 'learned' && rt.i_ref_trip ? ` (trip ${rt.i_ref_trip} is the lane's typical path)` : ''}. Dashed: the plan. Blue: where the truck kept to it. Coloured: each deviation — amber a detour, green a shortcut, violet a stop off the route, red a backtrack.`}
      actions={<Badge variant={rt.s_verdict === 'loss' ? 'danger' : rt.s_verdict === 'saving' ? 'success' : 'neutral'}>
        {rt.s_verdict === 'loss' ? 'loss against plan' : rt.s_verdict === 'saving' ? 'saving against plan' : 'on plan'}</Badge>}>
      <div className="grid grid-cols-1 xl:grid-cols-3 gap-5">
        <div className="xl:col-span-2">
          <GeoMap height={420} onReady={setMap} />
          <div className="flex flex-wrap gap-3 mt-2 text-xs text-gray-400">
            <span className="inline-flex items-center gap-1.5"><span className="w-5 border-t-2 border-dashed" style={{ borderColor: PALETTE.tooltipText }} />planned</span>
            <span className="inline-flex items-center gap-1.5"><span className="w-5 h-1 rounded" style={{ background: PALETTE.blue }} />driven, on the plan</span>
            {['detour', 'shortcut', 'off_route_stop', 'backtrack', 'alternate_route'].map(k => (
              <span key={k} className="inline-flex items-center gap-1.5"><span className="w-5 h-1 rounded" style={{ background: deviationColor(k) }} />{DEVIATION_LABEL[k].toLowerCase()}</span>
            ))}
          </div>
        </div>
        <div className="space-y-3">
          <div className="grid grid-cols-2 gap-2">
            <Field label="Planned" value={`${fmtKm(rt.d_planned_km)} · ${fmtDuration(rt.i_planned_transit_s)}`} />
            <Field label="Driven" value={`${fmtKm(rt.d_actual_km)} · ${fmtDuration(rt.i_actual_transit_s)}`} />
            <Field label="Extra distance" value={<ExtraKm km={rt.d_extra_km} pct={rt.d_extra_pct} />} />
            <Field label="Extra time" value={rt.i_extra_s == null ? '—' : `${rt.i_extra_s > 0 ? '+' : ''}${fmtDuration(rt.i_extra_s)}`} />
            <Field label="Off the route" value={`${fmtKm(rt.d_offroute_km)} · ${fmtDuration(rt.i_offroute_s)}`} />
            <Field label="Kept to the route" value={fmtPct(rt.d_adherence_pct, 0)} />
          </div>
          <div className="rounded-lg border border-gray-800 p-3 text-sm space-y-1.5">
            <div className="flex justify-between"><span className="text-gray-400">Planned cost</span><span className="tabular">{fmtInr(rt.d_cost_plan)}</span></div>
            <div className="flex justify-between"><span className="text-gray-400">Actual cost</span><span className="tabular">{fmtInr(rt.d_cost_actual)}</span></div>
            <div className="flex justify-between text-xs"><span className="text-gray-500 pl-3">distance</span><Inr v={rt.d_variance_km} /></div>
            <div className="flex justify-between text-xs"><span className="text-gray-500 pl-3">time</span><Inr v={rt.d_variance_time} /></div>
            <div className="flex justify-between border-t border-gray-800 pt-1.5 font-medium"><span>Against plan</span><Inr v={rt.d_variance} /></div>
            {rt.d_detention_h > 0 && (
              <div className="flex justify-between text-xs text-gray-400"><span>Detention beyond free time</span>
                <span>{rt.d_detention_h} h · {fmtInr(rt.d_detention_cost)}</span></div>
            )}
            {rt.d_margin != null && <div className="flex justify-between text-xs"><span className="text-gray-400">Margin</span><Inr v={rt.d_margin} /></div>}
            <p className="text-xs text-gray-600 pt-1">
              At ₹{d.settings.rates.per_km}/km and ₹{d.settings.rates.per_hour}/h — placeholder rates, set in .env (ROUTE_COST_PER_KM, ROUTE_COST_PER_HOUR); try others on the Routes page.
            </p>
          </div>
        </div>
      </div>
      {d.deviations.length > 0 && (
        <div className="mt-4">
          <DataTable dense rows={d.deviations} rowKey={x => x.i_seq}
            onRowClick={x => map?.setView([Number(x.d_leave_lat), Number(x.d_leave_long)], 13)}
            columns={[
              { key: 'i_seq', label: '#', render: x => <span className="text-gray-500">{x.i_seq}</span> },
              { key: 's_kind', label: 'Kind', render: x => <DeviationBadge kind={x.s_kind} /> },
              { key: 'dt_leave', label: 'Left the route', render: x => <span className="text-xs">{fmtDateTime(x.dt_leave)}</span> },
              { key: 'dt_back', label: 'Back on it', render: x => x.dt_back ? <span className="text-xs">{fmtDateTime(x.dt_back)}</span> : <span className="text-xs text-gray-500">not before arriving</span> },
              { key: 'i_duration_s', label: 'For', align: 'right', render: x => fmtDuration(x.i_duration_s) },
              { key: 'd_max_offset_m', label: 'Furthest', align: 'right', render: x => fmtMetres(x.d_max_offset_m) },
              { key: 'km', label: 'Driven / skipped', align: 'right', render: x => <span className="text-xs tabular">{fmtKm(x.d_actual_km)} / {fmtKm(x.d_planned_km)}</span> },
              { key: 'd_extra_km', label: 'Extra', align: 'right', render: x => <ExtraKm km={x.d_extra_km} /> },
              { key: 'i_stop_s', label: 'Stopped', align: 'right', render: x => fmtDuration(x.i_stop_s) },
            ]} />
        </div>
      )}
    </Card>
  );
}
