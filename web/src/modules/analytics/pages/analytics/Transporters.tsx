import { useMemo, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import {
  Trophy, ThumbsUp, ThumbsDown, Radar as RadarIcon, Box, Download, Target, Grid3x3,
} from 'lucide-react';
import PageContainer from '../../components/layout/PageContainer';
import Spinner from '../../components/ui/Spinner';
import ChartCard from '../../components/ui/ChartCard';
import DataTable from '../../components/ui/DataTable';
import ProgressBar from '../../components/ui/ProgressBar';
import HBarChart from '../../components/charts/HBarChart';
import ScatterBubbleChart from '../../components/charts/ScatterBubbleChart';
import RadarCompareChart from '../../components/charts/RadarCompareChart';
import BoxPlotChart from '../../components/charts/BoxPlotChart';
import MultiSelect from '../../components/ui/MultiSelect';
import QuadrantScatter, { QUADRANT_COLORS } from '../../components/charts/QuadrantScatter';
import { useApi } from '../../hooks/useApi';
import { useTTAFilters } from '../../components/tta/dashboard/FilterContext';
import { getDashBoxplot, getDashGroup, type GroupRow } from '../../services/ttaDashboard';
import { getReliabilityMatrix, type Quadrant } from '../../services/transporters';
import { downloadCsv } from '../../lib/csv';
import { tc } from '../../../../core/theme';

const QUADRANT_ORDER: Quadrant[] = ['critical', 'core', 'grow', 'review'];

export default function Transporters() {
  const { params, paramsKey } = useTTAFilters();
  const navigate = useNavigate();
  const [minTrips, setMinTrips] = useState(20);
  const [picks, setPicks] = useState<string[]>([]);
  const [sortBy, setSortBy] = useState('trips');
  const [sortOrder, setSortOrder] = useState<'asc' | 'desc'>('desc');

  const { data, loading } = useApi(() => getDashGroup('transporter', 1, params), [paramsKey]);
  const { data: box } = useApi(() => getDashBoxplot('transporter', 'transit_hours', 10, params), [paramsKey]);
  const { data: matrix } = useApi(
    () => getReliabilityMatrix(params as never, minTrips), [paramsKey, minTrips]);

  const matrixPoints = useMemo(() => (matrix?.carriers ?? []).map(c => ({
    name: c.name,
    x: c.trips,
    y: c.otd_pct,
    // Bubble area is freight share, so the eye lands on the carriers whose
    // failure would actually hurt rather than on whoever happens to be worst.
    z: c.share_pct ?? 1,
    quadrant: c.quadrant,
    quadrantLabel: c.quadrant_label,
    yLow: c.otd_ci_low,
    yHigh: c.otd_ci_high,
    detail: [
      `${c.share_pct ?? 0}% of all freight`,
      `judged on ${c.otd_judged_trips} delivered trips`,
      c.reliability_score != null ? `reliability score ${c.reliability_score}` : '',
    ].filter(Boolean),
  })), [matrix]);

  const rows = useMemo(() => {
    const r = [...(data?.rows ?? [])];
    r.sort((a: any, b: any) => {
      const av = a[sortBy] ?? -Infinity, bv = b[sortBy] ?? -Infinity;
      return sortOrder === 'asc' ? (av > bv ? 1 : -1) : (av < bv ? 1 : -1);
    });
    return r;
  }, [data, sortBy, sortOrder]);

  const qualified = useMemo(
    () => (data?.rows ?? []).filter(r => r.trips >= minTrips && r.otd_pct != null),
    [data, minTrips]);

  const best = useMemo(() => [...qualified].sort((a, b) => (b.otd_pct ?? 0) - (a.otd_pct ?? 0)).slice(0, 10), [qualified]);
  const worst = useMemo(() => [...qualified].sort((a, b) => (a.otd_pct ?? 0) - (b.otd_pct ?? 0)).slice(0, 10), [qualified]);

  // radar candidates: qualifying carriers, default top 3 by volume
  const radarPool = useMemo(() => (data?.rows ?? []).filter(r => r.trips >= minTrips), [data, minTrips]);
  const selected = picks.length >= 2 ? picks.slice(0, 4)
    : radarPool.slice(0, 3).map(r => r.name);

  const radarData = useMemo(() => {
    if (!radarPool.length) return [];
    const axes: { axis: string; key: keyof GroupRow; invert: boolean }[] = [
      { axis: 'OTD %', key: 'otd_pct', invert: false },
      { axis: 'Transit', key: 'avg_transit_hours', invert: true },
      { axis: 'Detention', key: 'avg_detention_hours', invert: true },
      { axis: 'Violations', key: 'violations_per_trip', invert: true },
      { axis: 'GPS uptime', key: 'avg_gps_uptime', invert: false },
    ];
    return axes.map(({ axis, key, invert }) => {
      const vals = radarPool.map(r => r[key] as number).filter(v => v != null && isFinite(v));
      const lo = Math.min(...vals), hi = Math.max(...vals);
      const row: Record<string, any> = { axis };
      for (const name of selected) {
        const r = radarPool.find(x => x.name === name);
        const v = r?.[key] as number | null;
        if (v == null || hi === lo) { row[name] = 50; continue; }
        const norm = (100 * (v - lo)) / (hi - lo);
        row[name] = Math.round(invert ? 100 - norm : norm);
      }
      return row;
    });
  }, [radarPool, selected]);

  const bubbles = qualified
    .filter(r => r.avg_transit_hours != null && r.otd_pct != null)
    .map(r => ({
      name: r.name, x: r.avg_transit_hours as number, y: r.otd_pct as number,
      z: r.trips, c: r.avg_detention_hours ?? undefined,
    }));

  const fmtH = (v: number | null) => (v != null ? `${v.toFixed(1)} h` : '—');

  return (
    <PageContainer title="🚚 Transporter Scorecard">
      <div className="flex items-center gap-4 flex-wrap mb-6">
        <label className="flex items-center gap-2 text-xs text-gray-400">
          Minimum trips to qualify
          <input type="range" min={5} max={100} step={5} value={minTrips}
            onChange={e => setMinTrips(Number(e.target.value))} className="accent-blue-600" />
          <span className="text-blue-400 font-semibold w-6">{minTrips}</span>
        </label>
        <button onClick={() => downloadCsv(rows, 'transporters.csv')}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs text-gray-300 border border-gray-700 bg-gray-800 hover:text-white">
          <Download className="w-3.5 h-3.5" /> Export CSV
        </button>
      </div>

      {/* ---------------- Volume vs reliability ---------------- */}
      <ChartCard title="🎯 Reliability Matrix — volume vs reliability"
        method={{
          formula: "x = trips carried, y = on-time rate, bubble area = share of all filtered freight. The vertical line is the median carrier's trip count; the horizontal line is the 95% service target. Quadrant follows from which side of each line a carrier falls.",
          plot: "quadrant",
          caveat: "Hover for the 95% Wilson band on each carrier's on-time rate. A carrier at 100% on six trips has not out-performed one at 94% on two hundred — it has not been measured.",
        }} icon={Grid3x3}
        iconColor="text-emerald-400" className="mb-6"
        explain={matrix
          ? `Each bubble is a carrier: across is how much freight it carries, up is its on-time rate, and bubble size is its share of the total. The lines split the panel at the ${matrix.axes.volume_split_label} and the ${matrix.axes.otd_target}% service target. The league table ranks who is best; this answers the different and more expensive question — where the freight is sitting relative to the service. Click a bubble to open that carrier.`
          : 'Loading…'}>
        {matrix ? (
          <>
            <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 mb-5">
              {QUADRANT_ORDER.map(q => {
                const s = matrix.quadrants[q];
                if (!s) return null;
                return (
                  <div key={q} className="bg-gray-950/60 border border-gray-800 rounded-lg p-3"
                    style={{ borderLeftColor: QUADRANT_COLORS[q], borderLeftWidth: 3 }}>
                    <p className="text-xs font-semibold" style={{ color: QUADRANT_COLORS[q] }}>
                      {s.label}
                    </p>
                    <p className="text-lg font-bold text-gray-100 mt-0.5">
                      {s.carriers}
                      <span className="text-xs text-gray-500 font-normal"> carriers · {s.share_pct ?? 0}% of freight</span>
                    </p>
                    <p className="text-xs text-gray-500 mt-1 leading-snug">{s.action}</p>
                  </div>
                );
              })}
            </div>
            <QuadrantScatter points={matrixPoints}
              xSplit={matrix.axes.volume_split} ySplit={matrix.axes.otd_target}
              xLabel="Trips carried" yLabel="On-time delivery %"
              xSplitLabel={matrix.axes.volume_split_label}
              ySplitLabel={`${matrix.axes.otd_target}% target`}
              onSelect={(n) => navigate(`/transporters/${encodeURIComponent(n)}`)} />
            <p className="text-xs text-gray-500 mt-3">
              On-time rates on few trips are uncertain — hover any bubble for its 95%
              confidence band. A carrier at 100% on six trips has not out-performed one
              at 94% on two hundred; it has simply not been measured yet.
            </p>
          </>
        ) : <Spinner />}
      </ChartCard>

      <ChartCard title="🏆 League Table"
        method={{
          formula: "One row per carrier over the filtered window. ‘Vs plan’ is average actual transit minus average quoted transit, so positive means consistently behind the carrier's OWN promised ETA rather than simply slow.",
          plot: "table",
        }} icon={Trophy} iconColor="text-amber-400"
        explain='One row per carrier over the filtered window. "Vs plan" positive = consistently behind promised schedule. Click headers to sort.'>
        {loading ? <Spinner /> : (
          <div className="max-h-[420px] overflow-y-auto">
            <DataTable
              sortBy={sortBy} sortOrder={sortOrder}
              onSort={(c) => { if (sortBy === c) setSortOrder(o => o === 'asc' ? 'desc' : 'asc'); else { setSortBy(c); setSortOrder('desc'); } }}
              columns={[
                { key: 'name', label: 'Transporter', sortable: true,
                  render: (r: GroupRow) => (
                    <Link to={`/transporters/${encodeURIComponent(r.name)}`}
                      className="text-blue-400 hover:text-blue-300 font-medium">{r.name}</Link>) },
                { key: 'trips', label: 'Trips', sortable: true },
                {
                  key: 'share_pct', label: 'Share', sortable: true,
                  render: (r: GroupRow) => <div className="w-20"><ProgressBar percent={r.share_pct ?? 0} /></div>,
                },
                {
                  key: 'otd_pct', label: 'OTD %', sortable: true,
                  render: (r: GroupRow) => r.otd_pct != null
                    ? <span className={r.otd_pct >= 95 ? 'text-emerald-400 font-semibold' : r.otd_pct >= 80 ? 'text-amber-400' : 'text-red-400 font-semibold'}>{r.otd_pct}%</span> : '—',
                },
                { key: 'avg_transit_hours', label: 'Transit', sortable: true, render: (r: GroupRow) => fmtH(r.avg_transit_hours) },
                {
                  key: 'schedule_variance_hours', label: 'Vs plan', sortable: true,
                  render: (r: GroupRow) => r.schedule_variance_hours != null
                    ? <span className={r.schedule_variance_hours > 0 ? 'text-red-400' : 'text-emerald-400'}>
                        {r.schedule_variance_hours > 0 ? '+' : ''}{r.schedule_variance_hours} h</span> : '—',
                },
                { key: 'avg_detention_hours', label: 'Detention', sortable: true, render: (r: GroupRow) => fmtH(r.avg_detention_hours) },
                { key: 'total_km', label: 'Total km', sortable: true },
                { key: 'violations_per_trip', label: 'Alerts/trip', sortable: true },
                { key: 'avg_gps_uptime', label: 'GPS %', sortable: true },
                { key: 'vehicles', label: 'Vehicles', sortable: true },
                { key: 'destinations', label: 'Dests', sortable: true },
              ]}
              data={rows}
            />
          </div>
        )}
      </ChartCard>

      <div className="grid lg:grid-cols-2 gap-6 mt-6">
        <ChartCard title="Best OTD %"
        method={{
          formula: "On-time rate, descending, over carriers meeting the minimum-trips slider.",
          plot: "hbar",
          caveat: "Reward with volume only after checking route difficulty — easy lanes flatter this list.",
        }} icon={ThumbsUp} iconColor="text-emerald-400"
          explain={`Top 10 among carriers with ≥ ${minTrips} trips — reward this list with more volume.`}>
          <HBarChart data={best} nameKey="name" valueKey="otd_pct" valueLabel="OTD %" color="#22c55e" />
        </ChartCard>
        <ChartCard title="Worst OTD %"
        method={{
          formula: "On-time rate, ascending, same qualifying set.",
          plot: "hbar",
          caveat: "These may hold the hardest lanes. Compare against the lane mix before acting.",
        }} icon={ThumbsDown} iconColor="text-red-400"
          explain="Check route difficulty before punishing — these may hold the hardest lanes.">
          <HBarChart data={worst} nameKey="name" valueKey="otd_pct" valueLabel="OTD %" color={tc(tc('#ef4444'))} />
        </ChartCard>
      </div>

      <ChartCard title="Risk Map"
        method={{
          formula: "x = average transit hours, y = on-time rate, bubble area = trips, colour = average detention (green→red, inverted so red is worse).",
          plot: "bubble",
        }} icon={Target} iconColor="text-orange-400" className="mt-6"
        explain="Bubble = carrier; size = trips, colour = detention (green→red). Danger zone = big bubbles bottom-right (lots of freight on slow, unreliable partners); top-left deserves more volume.">
        <ScatterBubbleChart data={bubbles} xLabel="Avg transit (h)" yLabel="OTD %"
          zLabel="Trips" cLabel="Avg detention (h)" invertColor yDomain={[0, 100]} height={380} />
      </ChartCard>

      <ChartCard title="🎯 Head-to-head Radar"
        method={{
          formula: "Five metrics normalised 0–100 across the qualifying carriers. Transit, detention and violations are inverted, so further from the centre is always better.",
          plot: "radar",
          caveat: "Normalisation is relative to the carriers on screen — adding or removing one moves every shape.",
        }} icon={RadarIcon} iconColor="text-purple-400" className="mt-6"
        explain="Five axes normalised 0–100 across qualifying carriers; time, detention and violations are inverted so bigger is always better. A lopsided shape is the weakness to raise at contract review."
        actions={
          <MultiSelect label="Compare (2–4)" options={radarPool.map(r => r.name)}
            selected={picks} onChange={v => setPicks(v.slice(0, 4))} />
        }>
        {radarData.length && selected.length >= 2
          ? <RadarCompareChart data={radarData} entities={selected} />
          : <p className="text-gray-500 text-sm py-8">Not enough qualifying carriers to compare — lower the minimum-trips slider.</p>}
      </ChartCard>

      <ChartCard title="Transit Time Spread"
        method={{
          formula: "Quartiles of transit hours per carrier, top 10 by volume. Whiskers reach 1.5 × the interquartile range; points beyond are drawn individually.",
          plot: "box",
        }} icon={Box} iconColor="text-cyan-400" className="mt-6"
        explain="Box plot per carrier (top 10 by volume). A short box = predictable partner; a tall box with many dots = erratic, forcing buffer stock downstream.">
        <BoxPlotChart groups={box?.groups ?? []} unit="h" />
      </ChartCard>
    </PageContainer>
  );
}
