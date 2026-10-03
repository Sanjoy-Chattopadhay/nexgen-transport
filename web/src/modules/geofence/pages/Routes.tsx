import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  AlertTriangle, Calculator, Coins, CornerUpRight, Gauge, Hourglass, Map as MapIcon, Navigation, ParkingCircle,
  Route as RouteIcon, TrendingDown, TrendingUp, Truck,
} from 'lucide-react';
import { api } from '../lib/api';
import { useApi } from '../hooks/useApi';
import {
  Badge, Card, DataTable, DateInput, ErrorBox, KPI, Note, PageHeader, Pagination, SearchInput, Select, Spinner, Toolbar,
  TripRef, useSort,
} from '../components/ui';
import { DeviationBadge, DEVIATION_LABEL, ExtraKm, Inr, KPIGrid, fmtInr } from '../components/drill';
import { BarList } from '../components/charts';
import { fmtDateTime, fmtDuration, fmtHours, fmtInt, fmtKm, fmtNum, fmtPct } from '../lib/format';

const RATES_KEY = 'geofence-intelligence.route-rates';

interface Rates { per_km: number; per_hour: number; detention_per_hour: number; revenue_per_km: number | null; free_hours?: number }

function loadRates(): Partial<Rates> {
  try { return JSON.parse(window.localStorage.getItem(RATES_KEY) || '{}'); } catch { return {}; }
}

export default function Routes() {
  const navigate = useNavigate();
  const [q, setQ] = useState('');
  const [verdict, setVerdict] = useState('');
  const [from, setFrom] = useState('');
  const [to, setTo] = useState('');
  const [lane, setLane] = useState<{ from: number; to: number; label: string } | null>(null);
  const [page, setPage] = useState(1);
  const [lanePage, setLanePage] = useState(1);
  const { sort, order, onSort } = useSort('variance', 'asc');
  const filters = { q, verdict, from, to, from_site: lane?.from, to_site: lane?.to };
  const list = useApi(() => api.routes({ ...filters, sort, order, page, page_size: 20 }),
    [q, verdict, from, to, lane?.from, lane?.to, sort, order, page]);
  const lanes = useApi(() => api.routeLanes({ q, from, to, page: lanePage, page_size: 12 }), [q, from, to, lanePage]);
  const reset = <T,>(fn: (v: T) => void) => (v: T) => { fn(v); setPage(1); };

  const d = list.data;
  const t = d?.totals;
  const server: Rates | undefined = d?.settings?.rates;
  const [rates, setRates] = useState<Partial<Rates>>(loadRates);
  useEffect(() => {
    try { window.localStorage.setItem(RATES_KEY, JSON.stringify(rates)); } catch { /* not kept */ }
  }, [rates]);
  const r: Rates | null = server ? {
    per_km: rates.per_km ?? server.per_km, per_hour: rates.per_hour ?? server.per_hour,
    detention_per_hour: rates.detention_per_hour ?? server.detention_per_hour,
    revenue_per_km: rates.revenue_per_km !== undefined ? rates.revenue_per_km : server.revenue_per_km,
  } : null;
  // The variance is linear in the rates, so the totals re-price exactly from the sums.
  const priced = useMemo(() => {
    if (!t || !r) return null;
    const dKm = (t.planned_km ?? 0) - (t.actual_km ?? 0);
    const dH = ((t.planned_s ?? 0) - (t.actual_s ?? 0)) / 3600;
    const actual = (t.actual_km ?? 0) * r.per_km + ((t.actual_s ?? 0) / 3600) * r.per_hour;
    const detention = (t.detention_h ?? 0) * r.detention_per_hour;
    const revenue = r.revenue_per_km ? (t.planned_km ?? 0) * r.revenue_per_km : null;
    return { variance: dKm * r.per_km + dH * r.per_hour, varianceKm: dKm * r.per_km, varianceTime: dH * r.per_hour,
      actual, plan: actual + dKm * r.per_km + dH * r.per_hour, detention, revenue,
      margin: revenue != null ? revenue - actual - detention : null };
  }, [t, r]);
  const custom = !!r && !!server && (r.per_km !== server.per_km || r.per_hour !== server.per_hour
    || r.detention_per_hour !== server.detention_per_hour || r.revenue_per_km !== server.revenue_per_km);

  const ctx = { ...filters };
  const drillTrips = (extra: object) => ({ dataset: 'route_trips', params: { ...ctx, preset: 'ok', ...extra } });

  return (
    <div className="animate-fade-in">
      <PageHeader title="Routes & cost"
        subtitle="Every journey against the route it should have taken: how far it really drove, where it left the route and why, and what the difference cost. Local moves inside a works are not journeys and are not judged."
        badges={d && <>
          <Badge variant={d.settings.mode === 'osrm' ? 'success' : 'info'}>
            {d.settings.mode === 'osrm' ? 'plans: OSRM road routes' : 'plans: learned from each lane'}
          </Badge>
        </>} />

      {d && d.settings.mode !== 'osrm' && (
        <div className="mb-5">
          <Note title="OSRM is not configured.">
            Each lane’s plan is learned from its own history: of the lane’s trips with clean GPS, the one whose path lies closest to all
            the others is the route trucks on that lane actually take, and the lane’s median distance and time are the planned figures.
            Set <code>OSRM_URL</code> and run <code>python -m geofencing.cli analyse-routes</code> to plan by road instead — then a truck
            that leaves its route is re-planned from where it is, as a navigation app does, and every reroute is counted.
          </Note>
        </div>
      )}

      {list.error && <ErrorBox error={list.error} onRetry={list.reload} />}
      {!t && list.loading && <Spinner label="Measuring routes" />}
      {t && priced && r && (
        <>
          <KPIGrid className="grid grid-cols-2 md:grid-cols-4 2xl:grid-cols-6 gap-3 mb-6">
            <KPI label="Journeys measured" value={fmtInt(t.trips)} icon={Truck} color="blue"
              hint={`${fmtInt(t.vehicles)} vehicles · ${fmtInt(d.status?.local)} local moves not judged`}
              drill={{ ...drillTrips({}), groups: ['verdict', 'transporter', 'lane', 'mode'], sort: 'variance', order: 'asc' }} />
            <KPI label="Planned distance" value={fmtKm(t.planned_km)} icon={MapIcon} color="gray"
              hint={fmtDuration(t.planned_s)}
              drill={{ ...drillTrips({ measure: 'planned_km' }), groups: ['lane', 'transporter'], sort: 'planned' }} />
            <KPI label="Driven" value={fmtKm(t.actual_km)} icon={RouteIcon} color="cyan" hint={fmtDuration(t.actual_s)}
              drill={{ ...drillTrips({ measure: 'km' }), groups: ['transporter', 'lane', 'vehicle'], sort: 'actual' }} />
            <KPI label="Extra distance" value={`${(t.extra_km ?? 0) > 0 ? '+' : ''}${fmtKm(t.extra_km)}`} icon={TrendingUp}
              color={(t.extra_km ?? 0) > 0 ? 'amber' : 'green'} hint={`${fmtKm(t.extra_km_over)} over plan on the trips that went over`}
              drill={{ ...drillTrips({ measure: 'extra_km' }), groups: ['transporter', 'lane', 'vehicle'], sort: 'extra' }} />
            <KPI label="Deviations" value={fmtInt(t.deviations)} icon={CornerUpRight} color="amber"
              hint={`${fmtInt(t.detours)} detours · ${fmtKm(t.offroute_km)} off route`}
              drill={{ dataset: 'route_deviations', params: { ...ctx }, groups: ['kind', 'transporter', 'lane', 'hour'], sort: 'extra' }} />
            <KPI label="Off-route stops" value={fmtInt(t.offroute_stops)} icon={ParkingCircle} color="purple"
              hint="left the road to stand, then came back"
              drill={{ dataset: 'route_deviations', params: { ...ctx, preset: 'off_route_stop' }, groups: ['transporter', 'lane', 'hour'], sort: 'duration' }} />
            <KPI label="Reroutes" value={d.settings.mode === 'osrm' ? fmtInt(t.reroutes) : '—'} icon={Navigation} color="cyan"
              hint={d.settings.mode === 'osrm' ? 'replanned mid-trip' : 'needs OSRM'}
              drill={d.settings.mode === 'osrm' ? { ...drillTrips({ preset: 'rerouted' }), groups: ['transporter', 'lane'] } : undefined}
              details={d.settings.mode === 'osrm' ? undefined : () => <p className="text-sm text-gray-400">Rerouting needs a router to replan with. With OSRM configured, a truck that stays off its route is given a new route from where it is to its destination, and the extra distance the new route adds is recorded as that reroute’s cost.</p>} />
            <KPI label="Kept to the route" value={fmtPct(t.adherence_pct, 0)} icon={Gauge} color="green"
              hint="share of judged fixes on the plan"
              drill={{ ...drillTrips({}), groups: ['transporter', 'lane', 'vehicle'], sort: 'adherence', order: 'asc' }} />
            <KPI label={custom ? 'Against plan (your rates)' : 'Against plan'} value={<Inr v={Math.round(priced.variance)} />} icon={Coins}
              color={priced.variance < 0 ? 'red' : 'green'} hint={`distance ${fmtInr(Math.round(priced.varianceKm))} · time ${fmtInr(Math.round(priced.varianceTime))}`}
              drill={{ ...drillTrips({ measure: 'variance' }), groups: ['transporter', 'lane', 'vehicle', 'verdict'], sort: 'variance', order: 'asc',
                note: custom ? 'records at the configured rates' : undefined }} />
            <KPI label="Journeys at a loss" value={fmtInt(t.loss_trips)} icon={TrendingDown} color="red"
              hint={`${fmtInr(t.loss)} at configured rates`}
              drill={{ ...drillTrips({ preset: 'loss', measure: 'variance' }), groups: ['transporter', 'lane', 'vehicle'], sort: 'variance', order: 'asc' }} />
            <KPI label="Journeys with a saving" value={fmtInt(t.saving_trips)} icon={TrendingUp} color="green"
              hint={`+${fmtInr(t.saving)} · ${fmtInt(t.on_plan_trips)} on plan`}
              drill={{ ...drillTrips({ preset: 'saving', measure: 'variance' }), groups: ['transporter', 'lane', 'vehicle'], sort: 'variance', order: 'desc' }} />
            <KPI label="Detention" value={fmtHours((t.detention_h ?? 0) * 3600, 0)} icon={Hourglass} color="amber"
              hint={`${fmtInr(Math.round(priced.detention))} beyond ${server?.free_hours ?? 4} h free at each end`}
              drill={{ ...drillTrips({ preset: 'detained', measure: 'detention_h' }), groups: ['transporter', 'lane'], sort: 'detention' }} />
          </KPIGrid>

          <div className="grid grid-cols-1 xl:grid-cols-3 gap-6 mb-6">
            <Card title="What-if rates" icon={Calculator}
              subtitle="Re-price every total at your own rates. Kept in this browser only; the configured rates (.env) stay as they are for everyone."
              actions={custom && <button className="text-xs text-blue-400" onClick={() => setRates({})}>reset</button>}>
              <div className="grid grid-cols-2 gap-3">
                <RateInput label="₹ per km (fuel, tyres, upkeep)" value={r.per_km} onChange={v => setRates(x => ({ ...x, per_km: v ?? undefined }))} />
                <RateInput label="₹ per hour on the road" value={r.per_hour} onChange={v => setRates(x => ({ ...x, per_hour: v ?? undefined }))} />
                <RateInput label="₹ per hour of detention" value={r.detention_per_hour} onChange={v => setRates(x => ({ ...x, detention_per_hour: v ?? undefined }))} />
                <RateInput label="Freight ₹ per planned km" value={r.revenue_per_km} optional
                  onChange={v => setRates(x => ({ ...x, revenue_per_km: v }))} />
              </div>
              <div className="mt-4 rounded-lg border border-gray-800 p-3 text-sm space-y-1.5">
                <Row label="Planned cost" value={fmtInr(Math.round(priced.plan))} />
                <Row label="Actual cost" value={fmtInr(Math.round(priced.actual))} />
                <Row label="Against plan" value={<Inr v={Math.round(priced.variance)} />} strong />
                <Row label="Detention" value={fmtInr(Math.round(priced.detention))} />
                {priced.revenue != null && <>
                  <Row label="Freight" value={fmtInr(Math.round(priced.revenue))} />
                  <Row label="Margin" value={<Inr v={Math.round(priced.margin!)} />} strong />
                </>}
              </div>
            </Card>
            <Card title="Why trucks left their route" icon={AlertTriangle} className="xl:col-span-2"
              subtitle="Each deviation, by what the truck did: a detour rejoined further along, a shortcut did the same in less distance, an off-route stop left the road to stand (fuel, food, rest, parking) and came back, a backtrack rejoined behind where it left, an alternate route never came back before arriving.">
              <BarList rows={(d.deviation_kinds || []).map((k: any) => ({
                key: k.s_kind, label: <DeviationBadge kind={k.s_kind} />, value: k.n,
                sub: `${(k.extra_km ?? 0) > 0 ? '+' : ''}${fmtKm(k.extra_km)} · ${fmtDuration(k.duration_s)}`,
              }))} />
              <p className="text-xs text-gray-500 mt-3">
                Thresholds: off the route beyond {d.settings.mode === 'osrm' ? d.settings.thresholds.osrm.off_m : d.settings.thresholds.learned.off_m} m
                for {fmtDuration(d.settings.thresholds.osrm.confirm_s)} and two fixes (or {fmtKm(d.settings.thresholds.osrm.escape_m / 1000)} at once),
                back on within {d.settings.mode === 'osrm' ? d.settings.thresholds.osrm.on_m : d.settings.thresholds.learned.on_m} m;
                the first and last {fmtKm((d.settings.mode === 'osrm' ? d.settings.thresholds.osrm.terminal_m : d.settings.thresholds.learned.terminal_m) / 1000)} of
                a journey are not judged.
              </p>
            </Card>
          </div>
        </>
      )}

      <Card title="Lanes" icon={MapIcon} className="mb-6"
        subtitle="Loading place → unloading place, worst first. Click a lane to see its journeys below.">
        {lanes.error ? <ErrorBox error={lanes.error} /> : (
          <>
            <DataTable dense rows={lanes.data?.items ?? []} loading={lanes.loading}
              onRowClick={x => { setLane({ from: x.i_from_site, to: x.i_to_site, label: `${x.s_from_site} → ${x.s_to_site}` }); setPage(1); }}
              columns={[
                { key: 'lane', label: 'Lane', render: x => <span className="text-gray-100 text-xs">{x.s_from_site} → {x.s_to_site}</span> },
                { key: 'trips', label: 'Journeys', align: 'right', render: x => fmtInt(x.trips) },
                { key: 's_mode', label: 'Plan', render: x => <span className="text-xs text-gray-400">{x.s_mode === 'osrm' ? 'OSRM' : `learned${x.sample ? ` · ${x.sample} trips` : ''}`}</span> },
                { key: 'planned_km', label: 'Planned', align: 'right', render: x => fmtKm(x.planned_km) },
                { key: 'actual_km', label: 'Avg driven', align: 'right', render: x => fmtKm(x.actual_km) },
                { key: 'extra_km', label: 'Avg extra', align: 'right', render: x => <ExtraKm km={x.extra_km} /> },
                { key: 'deviations', label: 'Deviations / journey', align: 'right', render: x => fmtNum(x.deviations, 2) },
                { key: 'adherence_pct', label: 'On route', align: 'right', render: x => fmtPct(x.adherence_pct, 0) },
                { key: 'variance', label: 'Against plan', align: 'right', render: x => <Inr v={x.variance} /> },
              ]} />
            <Pagination page={lanes.data?.page ?? 1} pages={lanes.data?.pages ?? 1} total={lanes.data?.total} onPage={setLanePage} />
          </>
        )}
      </Card>

      <Card title="Journeys" icon={Truck}>
        <div className="mb-4">
          <Toolbar>
            <SearchInput value={q} onChange={reset(setQ)} placeholder="Trip, vehicle, transporter, place" width="w-72" />
            <Select value={verdict} onChange={reset(setVerdict)} options={[
              { value: '', label: 'Every verdict' }, { value: 'loss', label: 'At a loss' },
              { value: 'saving', label: 'With a saving' }, { value: 'on_plan', label: 'On plan' }]} />
            <DateInput label="From" value={from} onChange={reset(setFrom)} />
            <DateInput label="To" value={to} onChange={reset(setTo)} />
            {lane && <Badge variant="info">{lane.label} <button className="ml-1" onClick={() => setLane(null)}>×</button></Badge>}
          </Toolbar>
        </div>
        <DataTable rows={d?.items ?? []} loading={list.loading} sort={sort} order={order}
          onSort={k => { onSort(k); setPage(1); }} rowKey={x => x.i_trip_no}
          onRowClick={x => navigate(`/geo/trips/${x.i_trip_no}`)}
          columns={[
            { key: 'trip', label: 'Trip', sortable: true, render: x => <TripRef trip={x.i_trip_no} strong /> },
            { key: 'vehicle', label: 'Vehicle', render: x => <div><p className="text-gray-100">{x.s_asset_id}</p><p className="text-xs text-gray-500">{x.s_trans_name || ''}</p></div> },
            { key: 'lane', label: 'From → to', render: x => <span className="text-xs text-gray-400">{x.s_from_site} → {x.s_to_site}</span> },
            { key: 'start', label: 'Left', sortable: true, render: x => <span className="text-xs">{fmtDateTime(x.dt_transit_from)}</span> },
            { key: 'planned', label: 'Plan', align: 'right', sortable: true, render: x => fmtKm(x.d_planned_km) },
            { key: 'actual', label: 'Driven', align: 'right', sortable: true, render: x => fmtKm(x.d_actual_km) },
            { key: 'extra', label: 'Extra', align: 'right', sortable: true, render: x => <ExtraKm km={x.d_extra_km} pct={x.d_extra_pct} /> },
            { key: 'deviations', label: 'Deviations', align: 'right', sortable: true, render: x => x.i_deviations ? (
              <span title={x.i_offroute_stops ? `${x.i_offroute_stops} off-route stops` : undefined}>{x.i_deviations}</span>) : '0' },
            { key: 'adherence', label: 'On route', align: 'right', sortable: true, render: x => fmtPct(x.d_adherence_pct, 0) },
            { key: 'variance', label: 'Against plan', align: 'right', sortable: true, render: x => <Inr v={x.d_variance} /> },
            { key: 'verdict', label: '', render: x => x.s_verdict === 'loss' ? <Badge variant="danger">loss</Badge>
              : x.s_verdict === 'saving' ? <Badge variant="success">saving</Badge> : <Badge>on plan</Badge> },
          ]} />
        <Pagination page={d?.page ?? 1} pages={d?.pages ?? 1} total={d?.total} onPage={setPage} />
        <p className="text-xs text-gray-600 mt-3">
          Kinds of deviation: {Object.values(DEVIATION_LABEL).map(v => v.toLowerCase()).join(', ')}. Money at the configured rates.
        </p>
      </Card>
    </div>
  );
}

function RateInput({ label, value, onChange, optional }: {
  label: string; value: number | null; onChange: (v: number | null) => void; optional?: boolean;
}) {
  const [v, setV] = useState(value == null ? '' : String(value));
  useEffect(() => { setV(value == null ? '' : String(value)); }, [value]);
  return (
    <label className="text-xs text-gray-500 flex flex-col gap-1">
      {label}
      <input value={v} inputMode="decimal" placeholder={optional ? 'not set' : ''}
        onChange={e => setV(e.target.value)}
        onBlur={() => { const n = Number(v); onChange(v.trim() === '' ? null : Number.isFinite(n) ? n : value); }}
        className="bg-field border border-gray-700 rounded-lg text-sm text-gray-200 px-3 py-1.5 focus:outline-none focus:border-blue-500 tabular" />
    </label>
  );
}

function Row({ label, value, strong }: { label: string; value: React.ReactNode; strong?: boolean }) {
  return (
    <div className={`flex justify-between ${strong ? 'border-t border-gray-800 pt-1.5 font-medium' : ''}`}>
      <span className="text-gray-400">{label}</span><span className="tabular">{value}</span>
    </div>
  );
}
