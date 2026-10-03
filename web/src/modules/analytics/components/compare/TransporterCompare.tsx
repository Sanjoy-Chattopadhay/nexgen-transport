import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { useSearchParams } from 'react-router-dom';
import type { LucideIcon } from 'lucide-react';
import {
  GitCompareArrows, Loader2, Trophy, Crown, Handshake, Target, Clock, Truck,
  Activity, TrendingUp, Box, Layers, Route as RouteIcon, Satellite, AlertTriangle,
  ThumbsUp, ThumbsDown, Info, X,
} from 'lucide-react';
import ChartCard from '../ui/ChartCard';
import DateRangeFilter from '../ui/DateRangeFilter';
import InfoDot from '../ui/InfoDot';
import MultiSelect from '../ui/MultiSelect';
import Spinner from '../ui/Spinner';
import BarChart from '../charts/BarChart';
import BoxPlotChart from '../charts/BoxPlotChart';
import DualAxisChart from '../charts/DualAxisChart';
import RadarCompareChart from '../charts/RadarCompareChart';
import GradePill, { otdClass } from '../transporters/GradePill';
import { useApi } from '../../hooks/useApi';
import { useDateRange } from '../../hooks/useDateRange';
import { useMinTrips } from '../../hooks/useMinTrips';
import {
  compareTransporters, listTransporters,
  type CarrierCompare, type FleetBenchmark, type TransporterComparison,
} from '../../services/transporters';
import { formatNumber, formatPercent, formatDistance } from '../../lib/formatters';
import { COMPARE_COLORS, MAX_COMPARE } from './palette';
import { tc } from '../../../../core/theme';

type Granularity = 'D' | 'W' | 'M';

/** null-safe numeric read — missing carriers carry `{}` for kpis. */
const n = (v: number | null | undefined): number | null =>
  v == null || !isFinite(v) ? null : v;

const hrs = (v: number | null | undefined) => (v == null ? '—' : `${v.toFixed(1)} h`);
const signedHrs = (v: number | null | undefined) =>
  v == null ? '—' : `${v > 0 ? '+' : ''}${v.toFixed(1)} h`;

const GROUPS = ['Scorecard', 'Volume & footprint', 'Reliability',
  'Time & speed', 'Fleet & compliance'] as const;
type Group = typeof GROUPS[number];

interface MetricDef {
  label: string;
  group: Group;
  /** what the cell shows — a formatted string or a chip like <GradePill> */
  fmt: (c: CarrierCompare) => ReactNode;
  /** comparable number; omit for non-comparable rows like Grade */
  val?: (c: CarrierCompare) => number | null;
  better?: 'high' | 'low';
  /** the fleet-median reference this metric is benchmarked against */
  fleet?: (f: FleetBenchmark) => ReactNode;
  hint?: string;
}

/**
 * The head-to-head rows. `better` drives both the green highlight and the win
 * tally, so only add it where one direction is unambiguously better — "Market
 * %" and "Share %" are deliberately left neutral because the right answer
 * depends on your sourcing strategy, not on the carrier.
 */
const METRICS: MetricDef[] = [
  { label: 'Grade', group: 'Scorecard', fmt: c => <GradePill grade={c.kpis.grade ?? '—'} /> },
  { label: 'Composite score', group: 'Scorecard', fmt: c => c.kpis.score ?? 'too few trips', val: c => n(c.kpis.score), better: 'high' },
  { label: 'Rank in fleet', group: 'Scorecard', fmt: c => (c.kpis.rank == null ? '—' : `#${c.kpis.rank}`), val: c => n(c.kpis.rank), better: 'low', fleet: f => `of ${formatNumber(f.qualified)} ranked` },

  { label: 'Trips', group: 'Volume & footprint', fmt: c => formatNumber(c.trips), val: c => c.trips, better: 'high', fleet: f => formatNumber(f.trips) },
  { label: 'Share of freight', group: 'Volume & footprint', fmt: c => formatPercent(c.kpis.share_pct), hint: 'Neutral — a bigger share is leverage, and also concentration risk.' },
  { label: 'Active days', group: 'Volume & footprint', fmt: c => c.kpis.active_days ?? '—', val: c => n(c.kpis.active_days), better: 'high' },
  { label: 'Vehicles deployed', group: 'Volume & footprint', fmt: c => c.kpis.vehicles ?? '—', val: c => n(c.kpis.vehicles), better: 'high' },
  { label: 'Trips per vehicle', group: 'Volume & footprint', fmt: c => c.kpis.trips_per_vehicle ?? '—', val: c => n(c.kpis.trips_per_vehicle), better: 'high' },
  { label: 'Destinations served', group: 'Volume & footprint', fmt: c => c.kpis.destinations ?? '—', val: c => n(c.kpis.destinations), better: 'high' },
  { label: 'Total distance', group: 'Volume & footprint', fmt: c => formatDistance(c.kpis.total_km), val: c => n(c.kpis.total_km), better: 'high', fleet: f => formatDistance(f.total_km) },

  { label: 'On-time delivery', group: 'Reliability', fmt: c => <span className={otdClass(c.kpis.otd_pct)}>{formatPercent(c.kpis.otd_pct)}</span>, val: c => n(c.kpis.otd_pct), better: 'high', fleet: f => formatPercent(f.median_otd_pct) },
  { label: 'Vs own promised ETA', group: 'Reliability', fmt: c => signedHrs(c.kpis.schedule_variance_hours), val: c => n(c.kpis.schedule_variance_hours), better: 'low', fleet: f => signedHrs(f.median_schedule_variance_hours), hint: 'Positive = habitually later than the ETA it quoted you.' },
  { label: 'Avg slip when late', group: 'Reliability', fmt: c => hrs(c.kpis.avg_delay_when_late_hours), val: c => n(c.kpis.avg_delay_when_late_hours), better: 'low', hint: 'How bad it is when it does go wrong — separate question from how often.' },
  { label: 'Transit variability (CV)', group: 'Reliability', fmt: c => (c.transit_spread.stats.cv_pct == null ? '—' : `${c.transit_spread.stats.cv_pct}%`), val: c => n(c.transit_spread.stats.cv_pct), better: 'low', hint: 'Spread as % of mean. Under 25% is plannable; over 50% forces buffer stock.' },

  { label: 'Avg transit', group: 'Time & speed', fmt: c => hrs(c.kpis.avg_transit_hours), val: c => n(c.kpis.avg_transit_hours), better: 'low', fleet: f => hrs(f.median_transit_hours) },
  { label: 'Median transit', group: 'Time & speed', fmt: c => hrs(c.kpis.median_transit_hours), val: c => n(c.kpis.median_transit_hours), better: 'low' },
  { label: 'Detention at plant', group: 'Time & speed', fmt: c => hrs(c.kpis.avg_detention_hours), val: c => n(c.kpis.avg_detention_hours), better: 'low', fleet: f => hrs(f.median_detention_hours) },
  { label: 'Plant-to-VIVO', group: 'Time & speed', fmt: c => hrs(c.kpis.avg_plant_vivo_hours), val: c => n(c.kpis.avg_plant_vivo_hours), better: 'low' },
  { label: 'Dispatch lead', group: 'Time & speed', fmt: c => hrs(c.kpis.avg_dispatch_lead_hours), val: c => n(c.kpis.avg_dispatch_lead_hours), better: 'low', fleet: f => hrs(f.median_dispatch_lead_hours) },
  { label: 'Avg trip distance', group: 'Time & speed', fmt: c => formatDistance(c.kpis.avg_distance_km), hint: 'Context, not a score — it explains transit-hour gaps between carriers.' },
  { label: 'Avg speed', group: 'Time & speed', fmt: c => (c.kpis.avg_speed_kmph == null ? '—' : `${c.kpis.avg_speed_kmph} km/h`), val: c => n(c.kpis.avg_speed_kmph), better: 'high', fleet: f => (f.median_speed_kmph == null ? '—' : `${f.median_speed_kmph} km/h`) },

  { label: 'Market (hired) trucks', group: 'Fleet & compliance', fmt: c => formatPercent(c.kpis.market_pct), hint: 'All-market means the carrier is really a broker — less control over quality.' },
  { label: 'Speed alerts / trip', group: 'Fleet & compliance', fmt: c => c.kpis.violations_per_trip ?? '—', val: c => n(c.kpis.violations_per_trip), better: 'low', fleet: f => f.median_violations_per_trip ?? '—' },
  { label: 'Speed alerts (total)', group: 'Fleet & compliance', fmt: c => formatNumber(c.kpis.speed_violations) },
  { label: 'GPS uptime', group: 'Fleet & compliance', fmt: c => formatPercent(c.kpis.avg_gps_uptime), val: c => n(c.kpis.avg_gps_uptime), better: 'high', fleet: f => formatPercent(f.median_gps_uptime) },
  { label: 'Drivers seen', group: 'Fleet & compliance', fmt: c => c.kpis.drivers ?? '—' },
];

/** Category mix as % of each carrier's own trips, so sizes don't drown shapes. */
function mixPercent(carriers: CarrierCompare[],
                    field: 'own_market' | 'vehicle_category' | 'delivery') {
  const cats = [...new Set(carriers.flatMap(c => c[field].map(m => m.name)))];
  const rows = carriers.map(c => {
    const total = c[field].reduce((s, m) => s + m.trips, 0) || 1;
    const row: Record<string, string | number> = { name: c.name };
    cats.forEach(cat => {
      const hit = c[field].find(m => m.name === cat);
      row[cat] = hit ? Math.round((hit.trips / total) * 1000) / 10 : 0;
    });
    return row;
  });
  return { cats, rows };
}

const MIX_COLORS = [tc('#3b82f6'), tc('#10b981'), tc('#f59e0b'), tc('#ef4444'), '#8b5cf6', tc('#06b6d4')];

export default function TransporterCompare() {
  const [searchParams, setSearchParams] = useSearchParams();
  const { from, to, preset, setPreset, setCustom } = useDateRange();
  const { minTrips, setMinTrips } = useMinTrips();
  const [granularity, setGranularity] = useState<Granularity>('M');

  // The pick list doubles as the fleet context strip above the selector.
  const { data: league, loading: leagueLoading } = useApi(
    () => listTransporters({ date_from: from, date_to: to }, minTrips),
    [from, to, minTrips]);
  const options = useMemo(() => (league?.data ?? []).map(t => t.name), [league]);

  const [picks, setPicks] = useState<string[]>(() =>
    (searchParams.get('carriers') ?? '').split('||').filter(Boolean).slice(0, MAX_COMPARE));
  const [result, setResult] = useState<TransporterComparison | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const run = useCallback(async (names: string[]) => {
    if (names.length < 2) return;
    setLoading(true); setError(null);
    try {
      const res = await compareTransporters(
        names, { date_from: from, date_to: to }, minTrips, granularity);
      setResult(res.data);
    } catch (e) {
      const err = e as { response?: { data?: { detail?: string } }; message?: string };
      setError(err.response?.data?.detail || err.message || 'Comparison failed');
      setResult(null);
    } finally { setLoading(false); }
    // Deliberately NOT depending on setSearchParams: its identity changes on
    // every URL change (including a tab switch), and this callback drives the
    // re-run effect below — so depending on it would refetch on every tab click.
  }, [from, to, minTrips, granularity]);

  // Mirror the active comparison into the URL so the view is shareable. Only the
  // `carriers` key: `tab` is the shell's to own, and writing it from here would
  // fight the tab buttons and bounce the user straight back to this tab.
  // The guard makes this idempotent, so it settles after one write.
  useEffect(() => {
    if (!result) return;
    const want = result.names.join('||');
    if (searchParams.get('carriers') === want) return;
    const next = new URLSearchParams(searchParams);
    next.set('carriers', want);
    setSearchParams(next, { replace: true });
  }, [result, searchParams, setSearchParams]);

  // A deep link (?carriers=A||B — what the league table's Compare button builds)
  // runs itself once on arrival.
  const booted = useRef(false);
  useEffect(() => {
    if (booted.current) return;
    booted.current = true;
    if (picks.length >= 2) run(picks);
  }, [picks, run]);

  // `run` changes identity exactly when the window, min-trips or grain changes,
  // so this re-runs an existing comparison instead of leaving stale numbers on
  // screen under new filters.
  const active = useRef<string[] | null>(null);
  useEffect(() => {
    if (active.current) run(active.current);
  }, [run]);
  useEffect(() => { if (result) active.current = result.names; }, [result]);

  const cmp = result?.carriers;
  // stable identity — `laneWins` below keys off it
  const names = useMemo(() => result?.names ?? [], [result]);
  const fleet = result?.fleet;

  const series = names.map((name, i) => ({
    key: name, label: name, color: COMPARE_COLORS[i % MAX_COMPARE],
  }));

  const bestIdx = useCallback((m: MetricDef): number | null => {
    if (!cmp || !m.better || !m.val) return null;
    const vals = cmp.map(m.val);
    if (vals.some(v => v == null)) return null;   // incomparable — no winner
    const nums = vals as number[];
    const best = m.better === 'high' ? Math.max(...nums) : Math.min(...nums);
    return nums.indexOf(best);
  }, [cmp]);

  const wins = useMemo(() => {
    const w = (cmp ?? []).map(() => 0);
    if (cmp) METRICS.forEach(m => { const b = bestIdx(m); if (b != null) w[b]++; });
    return w;
  }, [cmp, bestIdx]);
  const winnerIdx = wins.length ? wins.indexOf(Math.max(...wins)) : -1;

  // Lane-level wins: on the lanes they share, who is fastest most often. This is
  // the like-for-like verdict — the overall transit average is skewed by which
  // carrier happens to hold the long routes.
  const laneWins = useMemo(() => {
    const w = names.map(() => 0);
    (result?.shared_lanes ?? []).forEach(l => {
      let bi = -1, best = Infinity;
      l.cells.forEach((c, i) => {
        const v = c?.avg_transit_hours;
        if (v != null && v < best) { best = v; bi = i; }
      });
      if (bi >= 0) w[bi]++;
    });
    return w;
  }, [result, names]);
  const sharedLaneCount = result?.shared_lanes.length ?? 0;

  const radarData = useMemo(() => {
    if (!cmp) return [];
    const axes: { axis: string; val: (c: CarrierCompare) => number | null; invert: boolean }[] = [
      { axis: 'On-time', val: c => n(c.kpis.otd_pct), invert: false },
      { axis: 'Transit', val: c => n(c.kpis.avg_transit_hours), invert: true },
      { axis: 'Detention', val: c => n(c.kpis.avg_detention_hours), invert: true },
      { axis: 'Few alerts', val: c => n(c.kpis.violations_per_trip), invert: true },
      { axis: 'GPS uptime', val: c => n(c.kpis.avg_gps_uptime), invert: false },
      { axis: 'Keeps its ETA', val: c => n(c.kpis.schedule_variance_hours), invert: true },
    ];
    return axes.map(({ axis, val, invert }) => {
      const vals = cmp.map(val).filter((v): v is number => v != null);
      const lo = Math.min(...vals), hi = Math.max(...vals);
      const row: Record<string, string | number> = { axis };
      cmp.forEach(c => {
        const v = val(c);
        if (v == null || hi === lo) { row[c.name] = v == null ? 0 : 100; return; }
        const norm = (100 * (v - lo)) / (hi - lo);
        row[c.name] = Math.round(invert ? 100 - norm : norm);
      });
      return row;
    });
  }, [cmp]);

  const mixes = useMemo(() => cmp && ({
    own: mixPercent(cmp, 'own_market'),
    cat: mixPercent(cmp, 'vehicle_category'),
    del: mixPercent(cmp, 'delivery'),
  }), [cmp]);
  const mixSeries = (cats: string[]) =>
    cats.map((c, i) => ({ key: c, label: c, color: MIX_COLORS[i % MIX_COLORS.length] }));

  const toggle = (vals: string[]) => setPicks(vals.slice(0, MAX_COMPARE));
  const selectTop = () => setPicks(options.slice(0, MAX_COMPARE));

  return (
    <>
      <DateRangeFilter from={from} to={to} preset={preset} onPreset={setPreset} onCustom={setCustom}
        note={league?.fleet
          ? `${formatNumber(league.fleet.trips)} trips · ${formatNumber(league.fleet.transporters)} carriers in window`
          : undefined} />

      {/* ---------------- Selector ---------------- */}
      <div className="bg-gradient-to-br from-gray-900 to-gray-900/60 rounded-xl border border-gray-800 p-5 mb-6">
        <div className="flex items-center justify-between flex-wrap gap-3 mb-4">
          <div>
            <h2 className="text-lg font-semibold text-white flex items-center gap-2">
              <Handshake className="w-5 h-5 text-blue-400" /> Pick 2–{MAX_COMPARE} carriers to benchmark
            </h2>
            <p className="text-xs text-gray-500 mt-1">
              Every metric side by side, plus the lanes they both actually run — the only fair like-for-like.
            </p>
          </div>
          <button onClick={() => run(picks)} disabled={picks.length < 2 || loading}
            className="flex items-center gap-2 px-5 py-2.5 bg-blue-600 hover:bg-blue-700 text-white rounded-lg text-sm font-semibold disabled:opacity-40 transition-colors shadow-lg shadow-blue-600/20">
            {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <GitCompareArrows className="w-4 h-4" />}
            Compare {picks.length > 0 && `(${picks.length})`}
          </button>
        </div>

        <div className="flex flex-wrap items-center gap-2 mb-3">
          <MultiSelect label={`Carriers (2–${MAX_COMPARE})`} options={options}
            selected={picks} onChange={toggle} />
          <button onClick={selectTop} disabled={!options.length}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-emerald-700 bg-emerald-600/10 text-emerald-300 text-xs font-medium hover:bg-emerald-600/20 disabled:opacity-40">
            <Layers className="w-3.5 h-3.5" /> Top {MAX_COMPARE} by volume
          </button>
          {picks.length > 0 && (
            <button onClick={() => setPicks([])}
              className="flex items-center gap-1 px-2 py-1.5 text-xs text-gray-500 hover:text-red-400">
              <X className="w-3.5 h-3.5" /> Clear
            </button>
          )}
          <label className="flex items-center gap-2 text-xs text-gray-400 ml-auto">
            Minimum trips to rank
            <input type="range" min={1} max={50} step={1} value={minTrips}
              onChange={e => setMinTrips(Number(e.target.value))} className="accent-blue-600" />
            <span className="text-blue-400 font-semibold w-6">{minTrips}</span>
          </label>
        </div>

        {/* selected chips, colour-coded to every chart below */}
        {leagueLoading ? <Spinner /> : (
          <div className="flex flex-wrap gap-2">
            {picks.length === 0 && (
              <p className="text-sm text-gray-500 py-2">
                {options.length
                  ? 'No carriers picked yet — open the dropdown or take the top 4 by volume.'
                  : 'No carriers in this window. Widen the date range.'}
              </p>
            )}
            {picks.map((p, i) => (
              <span key={p} className="text-xs rounded-lg border px-3 py-2 bg-gray-800/50 text-gray-300 flex items-center gap-2"
                style={{ borderColor: COMPARE_COLORS[i % MAX_COMPARE] }}>
                <span className="w-2 h-2 rounded-full" style={{ background: COMPARE_COLORS[i % MAX_COMPARE] }} />
                {p}
                <button onClick={() => setPicks(picks.filter(x => x !== p))}
                  className="text-gray-600 hover:text-red-400"><X className="w-3 h-3" /></button>
              </span>
            ))}
          </div>
        )}
        {error && <p className="text-red-400 text-sm mt-3">{error}</p>}
        {!!result?.missing.length && (
          <p className="text-amber-400 text-xs mt-3 flex items-center gap-1.5">
            <AlertTriangle className="w-3.5 h-3.5" />
            No trips in this window for: {result.missing.join(', ')} — their columns will be empty.
          </p>
        )}
      </div>

      {loading && !cmp && <Spinner />}

      {cmp && cmp.length > 0 && (
        <>
          {/* ---------------- Winner banner ---------------- */}
          {winnerIdx >= 0 && (
            <div className="rounded-xl border border-amber-600/40 bg-gradient-to-r from-amber-500/10 to-transparent p-5 mb-6 flex items-center gap-4 flex-wrap">
              <Crown className="w-9 h-9 text-amber-400 shrink-0" />
              <div>
                <p className="text-xs uppercase tracking-wide text-amber-400/80">Wins the most metrics</p>
                <p className="text-xl font-bold text-white">
                  {cmp[winnerIdx].name}
                  <span className="text-sm font-normal text-gray-400 ml-2">
                    grade {cmp[winnerIdx].kpis.grade ?? '—'} · {formatNumber(cmp[winnerIdx].trips)} trips
                  </span>
                </p>
              </div>
              <div className="ml-auto flex gap-2 flex-wrap">
                {cmp.map((c, i) => (
                  <div key={c.name} className="px-3 py-1.5 rounded-lg bg-gray-900/60 border border-gray-800 text-center min-w-[74px]">
                    <span className="text-xs font-semibold block truncate max-w-[90px]"
                      style={{ color: COMPARE_COLORS[i % MAX_COMPARE] }} title={c.name}>{c.name}</span>
                    <p className="text-lg font-bold text-white leading-none">{wins[i]}</p>
                    <span className="text-xs text-gray-500">metric wins</span>
                    {sharedLaneCount > 0 && (
                      <p className="text-xs text-gray-500 mt-1 border-t border-gray-800 pt-1">
                        <span className="text-emerald-400 font-semibold">{laneWins[i]}</span>/{sharedLaneCount} lanes
                      </p>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* ---------------- Per-carrier summary cards ---------------- */}
          <div className="grid gap-4 mb-6" style={{ gridTemplateColumns: `repeat(${cmp.length}, minmax(0,1fr))` }}>
            {cmp.map((c, i) => (
              <div key={c.name} className="bg-gray-900 rounded-xl border-t-4 border border-gray-800 p-4"
                style={{ borderTopColor: COMPARE_COLORS[i % MAX_COMPARE] }}>
                <div className="flex items-center justify-between gap-2">
                  <span className="font-bold text-white truncate" title={c.name}>
                    {c.name}{i === winnerIdx && <Trophy className="inline w-4 h-4 text-amber-400 ml-1.5" />}
                  </span>
                  <GradePill grade={c.kpis.grade ?? '—'} />
                </div>
                <p className="text-xs text-gray-500 mt-0.5">
                  {c.kpis.rank != null ? `Rank #${c.kpis.rank}` : 'Unranked'} · score {c.kpis.score ?? '—'}
                </p>
                <div className="grid grid-cols-2 gap-2 mt-3 text-center">
                  <Stat icon={Truck} label="Trips" value={formatNumber(c.trips)} color="text-blue-400" />
                  <Stat icon={Target} label="On-time" value={formatPercent(c.kpis.otd_pct)} color="text-emerald-400" />
                  <Stat icon={Clock} label="Transit" value={hrs(c.kpis.avg_transit_hours)} color="text-amber-400" />
                  <Stat icon={Box} label="Detention" value={hrs(c.kpis.avg_detention_hours)} color="text-purple-400" />
                </div>
              </div>
            ))}
          </div>

          {/* ---------------- Radar + OTD trend ---------------- */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-6">
            <ChartCard title="Performance fingerprint"
      method={{
        formula: "Five metrics normalised 0-100 across only the carriers you picked. Transit, detention and violations are inverted so further from the centre is always better.",
        plot: "radar",
        caveat: "Normalisation is relative to the picked set, so a shape changes when you add or remove a carrier. It shows who is better on each axis, never by how much in real units.",
      }} icon={Activity}
              explain="Each axis normalised across the picked carriers — outer edge is best-in-group. Transit, detention, alerts and ETA slip are inverted so bigger is always better. A lopsided shape is the weakness to raise at contract review.">
              {radarData.length ? <RadarCompareChart data={radarData} entities={names} height={340} />
                : <Empty msg="Not enough data to plot." />}
            </ChartCard>
            <ChartCard title="On-time trend"
      method={{
        formula: "On-time rate per period, one line per picked carrier, on a shared period grid.",
        plot: "line",
        caveat: "A carrier with no trips in a period has a gap, not a zero.",
      }} icon={Target}
              explain="One line per carrier over the same period grid. A line sliding down while its volume holds is the earliest warning you get."
              actions={<GrainToggle value={granularity} onChange={setGranularity} />}>
              {result?.trend_otd.length
                ? <DualAxisChart data={result.trend_otd} xKey="period" height={340}
                    series={series.map(s => ({ ...s, type: 'line' as const, axis: 'left' as const }))} />
                : <Empty msg="No periods with data in this window." />}
            </ChartCard>
          </div>

          {/* ---------------- Head-to-head table ---------------- */}
          <div className="bg-gray-900 rounded-xl border border-gray-800 p-5 mb-6 overflow-x-auto">
            <h2 className="text-lg font-semibold text-white mb-1 flex items-center gap-2">
              <GitCompareArrows className="w-5 h-5 text-blue-400" /> Head-to-Head
            </h2>
            <p className="text-xs text-gray-500 mb-4">
              Green + trophy marks the best of the picked carriers on that row. The last column is the
              median carrier across all {formatNumber(fleet?.transporters ?? 0)} in the window — beating
              each other means little if you are all below it.
            </p>
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-gray-800">
                  <th className="px-3 py-2 text-left text-xs text-gray-500 uppercase">Metric</th>
                  {cmp.map((c, i) => (
                    <th key={c.name} className="px-3 py-2 text-left">
                      <span className="font-bold block truncate max-w-[180px]" title={c.name}
                        style={{ color: COMPARE_COLORS[i % MAX_COMPARE] }}>{c.name}</span>
                      <span className="block text-xs text-gray-500 font-normal">
                        {formatNumber(c.trips)} trips
                      </span>
                    </th>
                  ))}
                  <th className="px-3 py-2 text-left text-xs text-gray-500 uppercase whitespace-nowrap">
                    Fleet median
                  </th>
                </tr>
              </thead>
              <tbody>
                {GROUPS.map(g => (
                  <Fragment key={g}>
                    <tr className="bg-gray-800/30">
                      <td colSpan={cmp.length + 2}
                        className="px-3 py-1.5 text-xs uppercase tracking-wide text-gray-500 font-semibold">{g}</td>
                    </tr>
                    {METRICS.filter(m => m.group === g).map(m => {
                      const b = bestIdx(m);
                      return (
                        <tr key={m.label} className="border-b border-gray-800/50">
                          <td className="px-3 py-2 text-gray-400 text-xs">
                            <span className="inline-flex items-center gap-1.5">
                              {m.label}
                              {m.hint && <InfoDot what={m.hint} title={m.label} />}
                            </span>
                          </td>
                          {cmp.map((c, i) => (
                            <td key={c.name} className={`px-3 py-2 ${b === i ? 'text-emerald-400 font-semibold' : 'text-gray-200'}`}>
                              <span className="inline-flex items-center gap-1.5">
                                {m.fmt(c) ?? '—'}{b === i && <Trophy className="w-3 h-3" />}
                              </span>
                            </td>
                          ))}
                          <td className="px-3 py-2 text-gray-500 text-xs whitespace-nowrap">
                            {fleet && m.fleet ? m.fleet(fleet) : '—'}
                          </td>
                        </tr>
                      );
                    })}
                  </Fragment>
                ))}
              </tbody>
            </table>
          </div>

          {/* ---------------- Shared lanes: the like-for-like verdict ---------------- */}
          <ChartCard title="Same lane, different carrier"
      method={{
        formula: "Lanes that more than one picked carrier actually ran, with each carrier's own trips, on-time, transit and detention on that lane side by side.",
        plot: "table",
        caveat: "This is the only fair head-to-head. Comparing carriers on overall average transit mostly measures who holds the longer routes.",
      }} icon={RouteIcon} iconColor="text-emerald-400"
            className="mb-6"
            explain="Only the lanes more than one of these carriers actually runs. Comparing overall transit hours mostly measures who holds the longer routes; this removes the route from the comparison. Best transit on each lane is highlighted.">
            {sharedLaneCount === 0 ? (
              <p className="text-gray-500 text-sm py-6">
                These carriers share no lane in this window — they run separate networks, so the
                head-to-head above is comparing different routes. Widen the date range or pick
                carriers that overlap.
              </p>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-gray-800">
                      <th className="px-3 py-2 text-left text-xs text-gray-500 uppercase">Lane</th>
                      <th className="px-3 py-2 text-left text-xs text-gray-500 uppercase">Distance</th>
                      {names.map((name, i) => (
                        <th key={name} className="px-3 py-2 text-left text-xs">
                          <span className="font-bold block truncate max-w-[150px]" title={name}
                            style={{ color: COMPARE_COLORS[i % MAX_COMPARE] }}>{name}</span>
                          <span className="text-xs text-gray-500 font-normal">trips · OTD · transit</span>
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {result!.shared_lanes.map(l => {
                      let bi = -1, best = Infinity;
                      l.cells.forEach((c, i) => {
                        const v = c?.avg_transit_hours;
                        if (v != null && v < best) { best = v; bi = i; }
                      });
                      return (
                        <tr key={l.lane} className="border-b border-gray-800/50">
                          <td className="px-3 py-2 text-gray-200 text-xs">{l.lane}</td>
                          <td className="px-3 py-2 text-gray-500 text-xs whitespace-nowrap">
                            {formatDistance(l.avg_distance_km)}
                          </td>
                          {l.cells.map((c, i) => (
                            <td key={i} className="px-3 py-2 text-xs whitespace-nowrap">
                              {!c ? <span className="text-gray-700">did not run</span> : (
                                <span className={bi === i ? 'text-emerald-400 font-semibold' : 'text-gray-300'}>
                                  {c.trips} · <span className={bi === i ? '' : otdClass(c.otd_pct)}>{formatPercent(c.otd_pct)}</span> · {hrs(c.avg_transit_hours)}
                                  {bi === i && <Trophy className="inline w-3 h-3 ml-1" />}
                                </span>
                              )}
                            </td>
                          ))}
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </ChartCard>

          {/* ---------------- Consistency ---------------- */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-6">
            <ChartCard title="Transit-time spread"
      method={{
        formula: "Quartiles of transit hours per picked carrier; whiskers at 1.5x the interquartile range.",
        plot: "box",
      }} icon={Box} iconColor="text-cyan-400"
              explain="Box = middle half of trips, white line = median, red dots = outliers. A short box is a partner you can plan around; a long one costs you buffer stock even when the average looks fine.">
              <BoxPlotChart groups={result?.box_transit ?? []} unit="h" />
            </ChartCard>
            <ChartCard title="Detention spread"
      method={{
        formula: "Quartiles of plant detention hours per picked carrier.",
        plot: "box",
      }} icon={Clock} iconColor="text-red-400"
              explain="Hours lost at the plant before the wheels turn. If every carrier here has a fat box, the bottleneck is your loading bay, not the carriers.">
              <BoxPlotChart groups={result?.box_detention ?? []} unit="h" />
            </ChartCard>
          </div>

          {/* ---------------- Volume & transit trend ---------------- */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-6">
            <ChartCard title="Volume split over time"
      method={{
        formula: "Trips per period, one series per carrier, stacked to the period total.",
        plot: "stackedBar",
      }} icon={Layers}
              explain="Trips per period. Watch for one colour quietly taking over — that is allocation drifting without a decision being made.">
              {result?.trend_trips.length
                ? <BarChart data={result.trend_trips} xKey="period" series={series} showLegend height={300} />
                : <Empty msg="No periods with data." />}
            </ChartCard>
            <ChartCard title="Transit-time trend"
      method={{
        formula: "Mean transit hours per period, one line per carrier.",
        plot: "line",
        caveat: "Movement here can be route mix rather than performance — check the shared-lane table before concluding a carrier got slower.",
      }} icon={TrendingUp} iconColor="text-amber-400"
              explain="Average hours door-to-door. Lines rising together point at your lanes or loading, not at the partners.">
              {result?.trend_transit.length
                ? <DualAxisChart data={result.trend_transit} xKey="period" height={300}
                    series={series.map(s => ({ ...s, type: 'line' as const, axis: 'left' as const }))} />
                : <Empty msg="No periods with data." />}
            </ChartCard>
          </div>

          {/* ---------------- Mix (% of each carrier's own trips) ---------------- */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-6">
            <ChartCard title="Own vs market trucks"
      method={{
        formula: "Each carrier's trips split by owned versus hired truck.",
        plot: "stackedBar",
      }} icon={Truck} iconColor="text-purple-400"
              explain="Share of each carrier's own trips. Market (hired) trucks mean less control over vehicle quality and tracking — an all-market carrier is really a broker.">
              {mixes?.own.cats.length
                ? <BarChart data={mixes.own.rows} xKey="name" series={mixSeries(mixes.own.cats)}
                    stacked showLegend angledLabels height={300} />
                : <Empty msg="No fleet-type data." />}
            </ChartCard>
            <ChartCard title="Vehicle categories"
      method={{
        formula: "Each carrier's trips by derived vehicle class.",
        plot: "stackedBar",
      }} icon={Truck} iconColor="text-cyan-400"
              explain="What each carrier actually deploys on your freight, as a share of its own trips — the fit between your load profile and their fleet.">
              {mixes?.cat.cats.length
                ? <BarChart data={mixes.cat.rows} xKey="name" series={mixSeries(mixes.cat.cats)}
                    stacked showLegend angledLabels height={300} />
                : <Empty msg="No vehicle-category data." />}
            </ChartCard>
          </div>

          <ChartCard title="Delivery outcomes"
      method={{
        formula: "Each carrier's trips by recorded delivery status.",
        plot: "stackedBar",
        caveat: "Trips with no recorded status are excluded, so bars can represent different totals.",
      }} icon={Target} iconColor="text-emerald-400" className="mb-6"
            explain="How the provider classified each delivery, as a share of each carrier's trips. The red band is the part that costs you money.">
            {mixes?.del.cats.length
              ? <BarChart data={mixes.del.rows} xKey="name" series={mixSeries(mixes.del.cats)}
                  stacked showLegend angledLabels height={320} />
              : <Empty msg="No delivery-status data." />}
          </ChartCard>

          {/* ---------------- Per-carrier verdicts ---------------- */}
          <div className="grid gap-4 mb-6" style={{ gridTemplateColumns: `repeat(${cmp.length}, minmax(0,1fr))` }}>
            {cmp.map((c, i) => (
              <div key={c.name} className="bg-gray-900 rounded-xl border border-gray-800 p-4">
                <h3 className="text-sm font-semibold mb-3 truncate" title={c.name}
                  style={{ color: COMPARE_COLORS[i % MAX_COMPARE] }}>{c.name}</h3>
                {c.insights.length === 0 && (
                  <p className="text-xs text-gray-500">Too few trips in this window to assess.</p>
                )}
                <ul className="space-y-2">
                  {c.insights.map((ins, k) => (
                    <li key={k} className="flex gap-2 text-xs leading-relaxed">
                      {ins.tone === 'good' ? <ThumbsUp className="w-3.5 h-3.5 text-emerald-400 shrink-0 mt-0.5" />
                        : ins.tone === 'bad' ? <ThumbsDown className="w-3.5 h-3.5 text-red-400 shrink-0 mt-0.5" />
                        : <Info className="w-3.5 h-3.5 text-gray-500 shrink-0 mt-0.5" />}
                      <span className={ins.tone === 'good' ? 'text-emerald-200/90'
                        : ins.tone === 'bad' ? 'text-red-200/90' : 'text-gray-400'}>{ins.text}</span>
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </div>

          {/* ---------------- Each carrier's own top lanes ---------------- */}
          <ChartCard title="Busiest lanes per carrier"
      method={{
        formula: "Each picked carrier's highest-volume lanes.",
        plot: "table",
        caveat: "Two carriers with no overlap here cannot be compared on service — see the shared-lane table.",
      }} icon={Satellite} iconColor="text-blue-400"
            explain="Where each carrier's volume actually sits. Little overlap between these lists is itself the finding — you are not running a competition, you are running two separate networks.">
            <div className="grid gap-4" style={{ gridTemplateColumns: `repeat(${cmp.length}, minmax(0,1fr))` }}>
              {cmp.map((c, i) => (
                <div key={c.name}>
                  <p className="text-xs font-semibold mb-2 truncate" title={c.name}
                    style={{ color: COMPARE_COLORS[i % MAX_COMPARE] }}>{c.name}</p>
                  {c.lanes.length === 0 && <p className="text-xs text-gray-500">No lanes.</p>}
                  <ul className="space-y-1">
                    {c.lanes.slice(0, 8).map(l => (
                      <li key={l.lane} className="text-xs text-gray-400 flex justify-between gap-2">
                        <span className="truncate" title={l.lane}>{l.lane}</span>
                        <span className="text-gray-500 whitespace-nowrap">
                          {l.trips} · <span className={otdClass(l.otd_pct)}>{formatPercent(l.otd_pct)}</span>
                        </span>
                      </li>
                    ))}
                  </ul>
                </div>
              ))}
            </div>
          </ChartCard>
        </>
      )}
    </>
  );
}

function GrainToggle({ value, onChange }: { value: Granularity; onChange: (g: Granularity) => void }) {
  return (
    <div className="flex items-center gap-1">
      {(['D', 'W', 'M'] as Granularity[]).map(g => (
        <button key={g} onClick={() => onChange(g)}
          className={`px-2.5 py-1 rounded-lg text-xs font-medium transition-colors ${
            value === g ? 'bg-blue-600 text-white'
              : 'bg-gray-800 text-gray-400 hover:text-gray-200 border border-gray-700'}`}>
          {g === 'D' ? 'Daily' : g === 'W' ? 'Weekly' : 'Monthly'}
        </button>
      ))}
    </div>
  );
}

function Stat({ icon: Icon, label, value, color }:
  { icon: LucideIcon; label: string; value: ReactNode; color: string }) {
  return (
    <div className="bg-gray-800/40 rounded-lg py-2">
      <Icon className={`w-4 h-4 mx-auto mb-1 ${color}`} />
      <p className="text-sm font-bold text-white leading-none">{value ?? '—'}</p>
      <p className="text-xs text-gray-500 mt-0.5">{label}</p>
    </div>
  );
}

function Empty({ msg }: { msg: string }) {
  return <div className="h-[300px] flex items-center justify-center text-sm text-gray-500">{msg}</div>;
}
