import { Fragment, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import {
  GitCompareArrows, Loader2, Trophy, Route as RouteIcon, Gauge, Clock,
  IndianRupee, Activity, Filter, X, Search, Layers, TrendingUp, Crown,
} from 'lucide-react';
import Spinner from '../ui/Spinner';
import Badge from '../ui/Badge';
import BarChart from '../charts/BarChart';
import DualAxisChart from '../charts/DualAxisChart';
import RadarCompareChart from '../charts/RadarCompareChart';
import ChartCard from '../ui/ChartCard';
import MultiSelect from '../ui/MultiSelect';
import { useApi } from '../../hooks/useApi';
import { getTTATrips, compareTTATrips } from '../../services/tta';
import { formatNumber, formatDuration, formatDateTime } from '../../lib/formatters';
import { COMPARE_COLORS } from './palette';

type Trip = any;
const num = (v: any): number | null => {
  const n = typeof v === 'number' ? v : parseFloat(String(v ?? '').replace(/[,%₹\s]/g, ''));
  return isFinite(n) ? n : null;
};

interface MetricDef {
  label: string;
  group: 'Overview' | 'Efficiency' | 'Driving' | 'Cost';
  fmt: (t: Trip) => any;
  val?: (t: Trip) => number | null;
  better?: 'high' | 'low';
}

const METRICS: MetricDef[] = [
  { label: 'Route', group: 'Overview', fmt: t => t.overview.route },
  { label: 'Vehicle / Driver', group: 'Overview', fmt: t => `${t.overview.vehicle} · ${t.overview.driver ?? '—'}` },
  { label: 'Delivery', group: 'Overview', fmt: t => t.overview.delivery_status ?? '—' },
  { label: 'GPS Distance (km)', group: 'Overview', fmt: t => formatNumber(t.overview.kpis.distance_km), val: t => num(t.overview.kpis.distance_km) },
  { label: 'Transit Time', group: 'Overview', fmt: t => formatDuration(t.overview.kpis.transit_time_min), val: t => num(t.overview.kpis.transit_time_min), better: 'low' },
  { label: 'Utilization %', group: 'Efficiency', fmt: t => `${t.overview.kpis.utilization_pct ?? '—'}%`, val: t => num(t.overview.kpis.utilization_pct), better: 'high' },
  { label: 'Avg Moving Speed (km/h)', group: 'Efficiency', fmt: t => t.overview.kpis.avg_moving_speed, val: t => num(t.overview.kpis.avg_moving_speed), better: 'high' },
  { label: 'Max Speed (km/h)', group: 'Efficiency', fmt: t => t.overview.kpis.max_speed, val: t => num(t.overview.kpis.max_speed) },
  { label: 'Detention', group: 'Efficiency', fmt: t => formatDuration(t.overview.kpis.detention_min), val: t => num(t.overview.kpis.detention_min), better: 'low' },
  { label: 'Total Stops', group: 'Efficiency', fmt: t => t.stops_kpis.total_stops, val: t => num(t.stops_kpis.total_stops), better: 'low' },
  { label: 'Stopped Hours', group: 'Efficiency', fmt: t => t.stops_kpis.total_stop_hours, val: t => num(t.stops_kpis.total_stop_hours), better: 'low' },
  { label: 'Driving Score', group: 'Driving', fmt: t => t.driving.score, val: t => num(t.driving.score), better: 'high' },
  { label: 'Night Driving %', group: 'Driving', fmt: t => `${t.driving.kpis?.night_driving_pct ?? 0}%`, val: t => num(t.driving.kpis?.night_driving_pct), better: 'low' },
  { label: 'Overspeed %', group: 'Driving', fmt: t => `${t.speed?.kpis?.overspeed_pct ?? 0}%`, val: t => num(t.speed?.kpis?.overspeed_pct), better: 'low' },
  { label: 'Harsh Events', group: 'Driving', fmt: t => (t.driving.kpis?.harsh_accel ?? 0) + (t.driving.kpis?.harsh_brake ?? 0), val: t => (num(t.driving.kpis?.harsh_accel) ?? 0) + (num(t.driving.kpis?.harsh_brake) ?? 0), better: 'low' },
  { label: 'Total Cost (₹)', group: 'Cost', fmt: t => `₹${formatNumber(t.cost.total_cost_inr)}`, val: t => num(t.cost.total_cost_inr), better: 'low' },
  { label: 'Cost / km (₹)', group: 'Cost', fmt: t => `₹${t.cost.cost_per_km}`, val: t => num(t.cost.cost_per_km), better: 'low' },
  { label: 'Fuel Cost (₹)', group: 'Cost', fmt: t => `₹${formatNumber(t.cost.fuel_cost_inr)}`, val: t => num(t.cost.fuel_cost_inr), better: 'low' },
  { label: 'Idle Fuel Waste (₹)', group: 'Cost', fmt: t => `₹${formatNumber(t.cost.idle_waste_inr)}`, val: t => num(t.cost.idle_waste_inr), better: 'low' },
];

const GROUPS = ['Overview', 'Efficiency', 'Driving', 'Cost'] as const;

const routeOf = (t: { s_org_node_name: string | null; s_dest_node_name: string | null }) =>
  `${t.s_org_node_name ?? '?'} → ${t.s_dest_node_name ?? '?'}`;

export default function TripCompare() {
  const [searchParams] = useSearchParams();
  const { data: trips, loading: tripsLoading } = useApi(() => getTTATrips(1, 500));
  const items = trips?.items ?? [];

  const [selected, setSelected] = useState<number[]>(() =>
    (searchParams.get('trips') ?? '').split(',').map(Number).filter(n => n > 0).slice(0, 4));
  const [result, setResult] = useState<any>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Filters
  const [routeFilter, setRouteFilter] = useState('');       // single lane (same-route mode)
  const [vehicleFilter, setVehicleFilter] = useState<string[]>([]);
  const [statusFilter, setStatusFilter] = useState<string[]>([]);
  const [query, setQuery] = useState('');

  const routes = useMemo(() => {
    const m = new Map<string, number>();
    items.forEach(t => { const r = routeOf(t); m.set(r, (m.get(r) ?? 0) + 1); });
    return Array.from(m.entries()).sort((a, b) => b[1] - a[1]);
  }, [items]);
  const vehicles = useMemo(() => [...new Set(items.map(t => t.s_asset_id).filter(Boolean))] as string[], [items]);
  const statuses = useMemo(() => [...new Set(items.map(t => t.s_delivery_status).filter(Boolean))] as string[], [items]);

  const filtered = useMemo(() => items.filter(t => {
    if (routeFilter && routeOf(t) !== routeFilter) return false;
    if (vehicleFilter.length && !vehicleFilter.includes(t.s_asset_id ?? '')) return false;
    if (statusFilter.length && !statusFilter.includes(t.s_delivery_status ?? '')) return false;
    if (query) {
      const hay = `${t.i_trip_no} ${t.s_asset_id} ${t.s_driver_name} ${routeOf(t)}`.toLowerCase();
      if (!hay.includes(query.toLowerCase())) return false;
    }
    return true;
  }), [items, routeFilter, vehicleFilter, statusFilter, query]);

  const hasFilters = routeFilter || vehicleFilter.length || statusFilter.length || query;
  const clearFilters = () => { setRouteFilter(''); setVehicleFilter([]); setStatusFilter([]); setQuery(''); };

  const toggle = (no: number) => setSelected(prev =>
    prev.includes(no) ? prev.filter(x => x !== no) : prev.length >= 4 ? prev : [...prev, no]);

  const selectLane = () => setSelected(filtered.slice(0, 4).map(t => t.i_trip_no));

  const run = async () => {
    setLoading(true); setError(null);
    try {
      const res = await compareTTATrips(selected);
      setResult(res.data);
    } catch (e: any) {
      setError(e?.response?.data?.detail || e.message || 'Comparison failed');
    } finally { setLoading(false); }
  };

  // ---- Derived comparison data (built once per result) ----
  const cmp = result?.trips as Trip[] | undefined;
  const key = (t: Trip) => `t${t.overview.trip_no}`;
  const series = (cmp ?? []).map((t, i) => ({ key: key(t), label: `#${t.overview.trip_no}`, color: COMPARE_COLORS[i % 4] }));

  const bestIdx = (m: MetricDef): number | null => {
    if (!cmp || !m.better || !m.val) return null;
    const vals = cmp.map(m.val);
    if (vals.some(v => v == null)) return null;
    const nums = vals as number[];
    const best = m.better === 'high' ? Math.max(...nums) : Math.min(...nums);
    return nums.indexOf(best);
  };

  const wins = useMemo(() => {
    const w = (cmp ?? []).map(() => 0);
    if (cmp) METRICS.forEach(m => { const b = bestIdx(m); if (b != null) w[b]++; });
    return w;
  }, [cmp]);
  const winnerIdx = wins.length ? wins.indexOf(Math.max(...wins)) : -1;

  const phaseChart = useMemo(() => !cmp ? [] :
    ['loading', 'transit', 'unloading', 'closure'].map(k => {
      const row: any = { phase: k };
      cmp.forEach(t => { const p = t.phases.find((x: any) => x.key === k); row[key(t)] = p ? Math.round((p.duration_min ?? 0) / 6) / 10 : 0; });
      return row;
    }), [cmp]);

  const zoneChart = useMemo(() => !cmp ? [] :
    ['slow_pct', 'moderate_pct', 'normal_pct', 'high_pct'].map(z => {
      const row: any = { zone: z.replace('_pct', '') };
      cmp.forEach(t => { row[key(t)] = t.speed.zones?.[z] ?? 0; });
      return row;
    }), [cmp]);

  const histChart = useMemo(() => {
    if (!cmp) return [];
    const bands = [...new Set(cmp.flatMap(t => (t.speed.histogram ?? []).map((b: any) => b.band)))]
      .sort((a, b) => parseInt(a) - parseInt(b));
    return bands.map(band => {
      const row: any = { band };
      cmp.forEach(t => {
        const total = (t.speed.histogram ?? []).reduce((s: number, b: any) => s + b.count, 0) || 1;
        const hit = (t.speed.histogram ?? []).find((b: any) => b.band === band);
        row[key(t)] = hit ? Math.round((hit.count / total) * 1000) / 10 : 0;
      });
      return row;
    });
  }, [cmp]);

  const costChart = useMemo(() => !cmp ? [] :
    [['Fuel', 'fuel_cost_inr'], ['Driver', 'driver_cost_inr'], ['Idle waste', 'idle_waste_inr']].map(([label, k]) => {
      const row: any = { comp: label };
      cmp.forEach(t => { row[key(t)] = t.cost[k] ?? 0; });
      return row;
    }), [cmp]);

  // Cumulative distance vs elapsed hours (same-lane pacing overlay), forward-filled.
  const paceChart = useMemo(() => {
    if (!cmp) return [];
    const buckets = new Map<number, any>();
    cmp.forEach(t => {
      const s = t.progress ?? [];
      if (!s.length) return;
      const t0 = new Date(s[0].t).getTime();
      s.forEach((p: any) => {
        const h = Math.round(((new Date(p.t).getTime() - t0) / 3600000) * 2) / 2;
        const row = buckets.get(h) ?? { h };
        row[key(t)] = Math.max(row[key(t)] ?? 0, p.km);
        buckets.set(h, row);
      });
    });
    const rows = Array.from(buckets.values()).sort((a, b) => a.h - b.h);
    const last: Record<string, number> = {};
    rows.forEach(r => cmp.forEach(t => {
      const k = key(t);
      if (r[k] == null) r[k] = last[k] ?? null; else last[k] = r[k];
    }));
    return rows;
  }, [cmp]);

  const radarData = useMemo(() => {
    if (!cmp) return [];
    const axes: { axis: string; val: (t: Trip) => number; better: 'high' | 'low' }[] = [
      { axis: 'Utilization', val: t => num(t.overview.kpis.utilization_pct) ?? 0, better: 'high' },
      { axis: 'Avg Speed', val: t => num(t.overview.kpis.avg_moving_speed) ?? 0, better: 'high' },
      { axis: 'Driving', val: t => num(t.driving.score) ?? 0, better: 'high' },
      { axis: 'On-Time', val: t => -(num(t.overview.delivery_delta_min) ?? 0), better: 'high' },
      { axis: 'Cost/km', val: t => num(t.cost.cost_per_km) ?? 0, better: 'low' },
      { axis: 'Few Stops', val: t => num(t.stops_kpis.total_stops) ?? 0, better: 'low' },
    ];
    return axes.map(a => {
      const vals = cmp.map(a.val);
      const best = a.better === 'high' ? Math.max(...vals) : Math.min(...vals);
      const worst = a.better === 'high' ? Math.min(...vals) : Math.max(...vals);
      const row: any = { axis: a.axis };
      cmp.forEach((t, i) => { row[`#${t.overview.trip_no}`] = best === worst ? 100 : Math.round(((vals[i] - worst) / (best - worst)) * 90 + 10); });
      return row;
    });
  }, [cmp]);
  const entities = (cmp ?? []).map(t => `#${t.overview.trip_no}`);

  return (
    <>
      {/* ---------------- Selector + Filters ---------------- */}
      <div className="bg-gradient-to-br from-gray-900 to-gray-900/60 rounded-xl border border-gray-800 p-5 mb-6">
        <div className="flex items-center justify-between flex-wrap gap-3 mb-4">
          <div>
            <h2 className="text-lg font-semibold text-white flex items-center gap-2">
              <GitCompareArrows className="w-5 h-5 text-blue-400" /> Pick 2–4 trips to benchmark
            </h2>
            <p className="text-xs text-gray-500 mt-1">Same lane, different days — or same vehicle, different drivers. See who wins.</p>
          </div>
          <button onClick={run} disabled={selected.length < 2 || loading}
            className="flex items-center gap-2 px-5 py-2.5 bg-blue-600 hover:bg-blue-700 text-white rounded-lg text-sm font-semibold disabled:opacity-40 transition-colors shadow-lg shadow-blue-600/20">
            {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <GitCompareArrows className="w-4 h-4" />}
            Compare {selected.length > 0 && `(${selected.length})`}
          </button>
        </div>

        {/* Filters bar */}
        <div className="flex flex-wrap items-center gap-2 mb-3">
          <div className="flex items-center gap-1.5 text-xs text-gray-500"><Filter className="w-3.5 h-3.5" /> Filter</div>
          <select value={routeFilter} onChange={e => setRouteFilter(e.target.value)}
            className="bg-gray-800 border border-gray-700 rounded-lg px-3 py-1.5 text-xs text-gray-200 outline-none focus:border-blue-600 max-w-[280px]">
            <option value="">All routes ({routes.length})</option>
            {routes.map(([r, c]) => <option key={r} value={r}>{r} ({c})</option>)}
          </select>
          <MultiSelect label="Vehicle" options={vehicles} selected={vehicleFilter} onChange={setVehicleFilter} />
          <MultiSelect label="Delivery status" options={statuses} selected={statusFilter} onChange={setStatusFilter} />
          <div className="relative">
            <Search className="w-3.5 h-3.5 text-gray-500 absolute left-2.5 top-1/2 -translate-y-1/2" />
            <input value={query} onChange={e => setQuery(e.target.value)} placeholder="Search trip / driver…"
              className="bg-gray-800 border border-gray-700 rounded-lg pl-8 pr-3 py-1.5 text-xs text-gray-200 outline-none focus:border-blue-600 w-48" />
          </div>
          {routeFilter && (
            <button onClick={selectLane}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-emerald-700 bg-emerald-600/10 text-emerald-300 text-xs font-medium hover:bg-emerald-600/20">
              <Layers className="w-3.5 h-3.5" /> Select up to 4 on this lane
            </button>
          )}
          {hasFilters && (
            <button onClick={clearFilters} className="flex items-center gap-1 px-2 py-1.5 text-xs text-gray-500 hover:text-red-400">
              <X className="w-3.5 h-3.5" /> Clear
            </button>
          )}
          <span className="ml-auto text-xs text-gray-500">{filtered.length} trip{filtered.length !== 1 ? 's' : ''}</span>
        </div>

        {tripsLoading ? <Spinner /> : (
          <div className="flex flex-wrap gap-2 max-h-52 overflow-y-auto pr-1">
            {filtered.length === 0 && <p className="text-sm text-gray-500 py-4">No trips match these filters.</p>}
            {filtered.map(t => {
              const on = selected.includes(t.i_trip_no);
              const idx = selected.indexOf(t.i_trip_no);
              return (
                <button key={t.i_trip_no} onClick={() => toggle(t.i_trip_no)}
                  className={`text-left text-xs rounded-lg border px-3 py-2 transition-colors ${
                    on ? 'bg-blue-600/20 border-blue-500 text-blue-200' : 'bg-gray-800/50 border-gray-700 text-gray-400 hover:border-gray-500'}`}
                  style={on ? { borderColor: COMPARE_COLORS[idx % 4] } : undefined}>
                  <span className="font-semibold flex items-center gap-1.5">
                    {on && <span className="w-2 h-2 rounded-full" style={{ background: COMPARE_COLORS[idx % 4] }} />}
                    {t.i_trip_no} · {t.s_asset_id}
                  </span>
                  <span className="block text-xs opacity-70">{routeOf(t)} · {formatDateTime(t.dt_trip_start)}</span>
                </button>
              );
            })}
          </div>
        )}
        {error && <p className="text-red-400 text-sm mt-3">{error}</p>}
      </div>

      {cmp && (
        <>
          {/* ---------------- Winner banner ---------------- */}
          {winnerIdx >= 0 && (
            <div className="rounded-xl border border-amber-600/40 bg-gradient-to-r from-amber-500/10 to-transparent p-5 mb-6 flex items-center gap-4 flex-wrap">
              <Crown className="w-9 h-9 text-amber-400 shrink-0" />
              <div>
                <p className="text-xs uppercase tracking-wide text-amber-400/80">Overall leader</p>
                <p className="text-xl font-bold text-white">
                  Trip #{cmp[winnerIdx].overview.trip_no}
                  <span className="text-sm font-normal text-gray-400 ml-2">{cmp[winnerIdx].overview.vehicle} · {cmp[winnerIdx].overview.route}</span>
                </p>
              </div>
              <div className="ml-auto flex gap-2 flex-wrap">
                {cmp.map((t, i) => (
                  <div key={i} className="px-3 py-1.5 rounded-lg bg-gray-900/60 border border-gray-800 text-center">
                    <span className="text-xs font-semibold" style={{ color: COMPARE_COLORS[i % 4] }}>#{t.overview.trip_no}</span>
                    <p className="text-lg font-bold text-white leading-none">{wins[i]}</p>
                    <span className="text-xs text-gray-500">wins</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* ---------------- Per-trip summary cards ---------------- */}
          <div className="grid gap-4 mb-6" style={{ gridTemplateColumns: `repeat(${cmp.length}, minmax(0,1fr))` }}>
            {cmp.map((t, i) => (
              <div key={i} className="bg-gray-900 rounded-xl border-t-4 border border-gray-800 p-4" style={{ borderTopColor: COMPARE_COLORS[i % 4] }}>
                <div className="flex items-center justify-between">
                  <span className="font-bold text-white">#{t.overview.trip_no}{i === winnerIdx && <Trophy className="inline w-4 h-4 text-amber-400 ml-1.5" />}</span>
                  <Badge label={t.overview.delivery_status ?? '—'}
                    variant={/on time|early|before/i.test(t.overview.delivery_status ?? '') ? 'success' : /delay|late/i.test(t.overview.delivery_status ?? '') ? 'danger' : 'neutral'} />
                </div>
                <p className="text-xs text-gray-500 mt-0.5 truncate" title={t.overview.route}>{t.overview.vehicle} · {t.overview.route}</p>
                <div className="grid grid-cols-2 gap-2 mt-3 text-center">
                  <Stat icon={Gauge} label="Score" value={t.driving.score} color="text-blue-400" />
                  <Stat icon={RouteIcon} label="Distance" value={`${formatNumber(t.overview.kpis.distance_km)} km`} color="text-emerald-400" />
                  <Stat icon={Clock} label="Transit" value={formatDuration(t.overview.kpis.transit_time_min)} color="text-amber-400" />
                  <Stat icon={IndianRupee} label="Cost/km" value={`₹${t.cost.cost_per_km}`} color="text-purple-400" />
                </div>
              </div>
            ))}
          </div>

          {/* ---------------- Radar + Pace ---------------- */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-6">
            <ChartCard title="Performance fingerprint"
      method={{
        formula: "Trip metrics normalised 0-100 across the picked trips, with lower-is-better axes inverted.",
        plot: "radar",
        caveat: "Relative to the picked trips only.",
      }} icon={Activity} explain="Each axis normalised across the selected trips — outer edge is best-in-group.">
              <RadarCompareChart data={radarData} entities={entities} height={340} />
            </ChartCard>
            <ChartCard title="Pace on the lane"
      method={{
        formula: "Cumulative distance against elapsed time for each trip, so a flat stretch is a halt.",
        plot: "line",
        caveat: "Steeper is faster. Flat segments are where the time went.",
      }} icon={TrendingUp} explain="Cumulative distance vs elapsed hours — the steeper line reaches destination sooner.">
              {paceChart.length > 1
                ? <DualAxisChart data={paceChart} xKey="h" height={340}
                    series={series.map(s => ({ key: s.key, label: s.label, color: s.color, type: 'line' as const }))} />
                : <Empty msg="Not enough GPS track to plot pacing." />}
            </ChartCard>
          </div>

          {/* ---------------- Head-to-head table ---------------- */}
          <div className="bg-gray-900 rounded-xl border border-gray-800 p-5 mb-6 overflow-x-auto">
            <h2 className="text-lg font-semibold text-white mb-4 flex items-center gap-2"><GitCompareArrows className="w-5 h-5 text-blue-400" /> Head-to-Head</h2>
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-gray-800">
                  <th className="px-3 py-2 text-left text-xs text-gray-500 uppercase">Metric</th>
                  {cmp.map((t, i) => (
                    <th key={i} className="px-3 py-2 text-left">
                      <span className="font-bold" style={{ color: COMPARE_COLORS[i % 4] }}>Trip {t.overview.trip_no}</span>
                      <span className="block text-xs text-gray-500 font-normal">{t.overview.vehicle}</span>
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {GROUPS.map(g => (
                  <Fragment key={g}>
                    <tr className="bg-gray-800/30">
                      <td colSpan={cmp.length + 1} className="px-3 py-1.5 text-xs uppercase tracking-wide text-gray-500 font-semibold">{g}</td>
                    </tr>
                    {METRICS.filter(m => m.group === g).map(m => {
                      const b = bestIdx(m);
                      return (
                        <tr key={m.label} className="border-b border-gray-800/50">
                          <td className="px-3 py-2 text-gray-400 text-xs">{m.label}</td>
                          {cmp.map((t, i) => (
                            <td key={i} className={`px-3 py-2 ${b === i ? 'text-emerald-400 font-semibold' : 'text-gray-200'}`}>
                              <span className="inline-flex items-center gap-1.5">{m.fmt(t) ?? '-'}{b === i && <Trophy className="w-3 h-3" />}</span>
                            </td>
                          ))}
                        </tr>
                      );
                    })}
                  </Fragment>
                ))}
              </tbody>
            </table>
          </div>

          {/* ---------------- Charts grid ---------------- */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-6">
            <ChartCard title="Lifecycle phases (hours)"
      method={{
        formula: "Each trip split into its recorded phases, stacked to total elapsed time.",
        plot: "stackedBar",
      }} icon={Clock} explain="Time spent in loading, transit, unloading and closure.">
              <BarChart data={phaseChart} xKey="phase" series={series} height={260} />
            </ChartCard>
            <ChartCard title="Speed zones (% of moving time)"
      method={{
        formula: "Share of each trip's MOVING time spent in each speed band; parked time excluded.",
        plot: "stackedBar",
        caveat: "Percentages are of moving time, not of the trip, so two trips with very different halt times are still comparable.",
      }} icon={Gauge} explain="How the moving time splits across speed bands.">
              <BarChart data={zoneChart} xKey="zone" series={series} height={260} />
            </ChartCard>
            <ChartCard title="Speed distribution (% of pings)"
      method={{
        formula: "Share of each trip's pings falling in each speed band.",
        plot: "stackedBar",
        caveat: "Ping-weighted, not time-weighted: a tracker reporting at a different rate shifts this even for identical driving.",
      }} icon={Activity} explain="Share of GPS pings in each 10 km/h band — normalised so trips of different length compare fairly.">
              <BarChart data={histChart} xKey="band" series={series} height={260} />
            </ChartCard>
            <ChartCard title="Cost breakdown (₹)"
      method={{
        formula: "Cost derived from the trip's distance, duration and halt time using the operator-editable rates on the cost-model screen.",
        plot: "stackedBar",
        caveat: "These are modelled costs from configured rates, not invoiced amounts. They are only as meaningful as those inputs.",
      }} icon={IndianRupee} explain="Fuel, driver wage and idle-fuel waste, side by side.">
              <BarChart data={costChart} xKey="comp" series={series} height={260} />
            </ChartCard>
          </div>
        </>
      )}
    </>
  );
}

function Stat({ icon: Icon, label, value, color }: { icon: any; label: string; value: any; color: string }) {
  return (
    <div className="bg-gray-800/40 rounded-lg py-2">
      <Icon className={`w-4 h-4 mx-auto mb-1 ${color}`} />
      <p className="text-sm font-bold text-white leading-none">{value ?? '—'}</p>
      <p className="text-xs text-gray-500 mt-0.5">{label}</p>
    </div>
  );
}

function Empty({ msg }: { msg: string }) {
  return <div className="h-[340px] flex items-center justify-center text-sm text-gray-500">{msg}</div>;
}
