import { useMemo, useState } from 'react';
import { Route as RouteIcon, TrendingDown, Grid3X3, Download, Scale, MoveDiagonal } from 'lucide-react';
import PageContainer from '../../components/layout/PageContainer';
import Spinner from '../../components/ui/Spinner';
import ChartCard from '../../components/ui/ChartCard';
import DataTable from '../../components/ui/DataTable';
import HBarChart from '../../components/charts/HBarChart';
import ScatterBubbleChart from '../../components/charts/ScatterBubbleChart';
import TreemapChart from '../../components/charts/TreemapChart';
import { useApi } from '../../hooks/useApi';
import { useTTAFilters } from '../../components/tta/dashboard/FilterContext';
import { getDashGroup, type GroupRow } from '../../services/ttaDashboard';
import { downloadCsv } from '../../lib/csv';
import { tc } from '../../../../core/theme';

export default function Lanes() {
  const { params, paramsKey } = useTTAFilters();
  const [sortBy, setSortBy] = useState('trips');
  const [sortOrder, setSortOrder] = useState<'asc' | 'desc'>('desc');

  const { data, loading } = useApi(() => getDashGroup('destination', 1, params), [paramsKey]);
  const all = data?.rows ?? [];
  // Statistical floor scales with dataset size: demanding 3+ trips per lane is
  // right at fleet scale but hides everything on a small/filtered dataset.
  const minLaneTrips = all.length >= 20 ? 3 : 1;
  const minSlowTrips = all.length >= 20 ? 10 : 1;
  const minVarTrips = all.length >= 20 ? 5 : 1;
  const lanes = useMemo(() => all.filter(r => r.trips >= minLaneTrips), [all, minLaneTrips]);

  const rows = useMemo(() => {
    const r = [...lanes];
    r.sort((a: any, b: any) => {
      const av = a[sortBy] ?? -Infinity, bv = b[sortBy] ?? -Infinity;
      return sortOrder === 'asc' ? (av > bv ? 1 : -1) : (av < bv ? 1 : -1);
    });
    return r;
  }, [lanes, sortBy, sortOrder]);

  // distance vs transit scatter + linear fit
  const scatter = lanes
    .filter(r => r.avg_distance_km != null && r.avg_transit_hours != null)
    .map(r => ({
      name: r.name, x: r.avg_distance_km as number, y: r.avg_transit_hours as number,
      z: r.trips, c: r.otd_pct ?? undefined,
    }));

  const fit = useMemo(() => {
    if (scatter.length < 2) return null;
    const n = scatter.length;
    const sx = scatter.reduce((a, p) => a + p.x, 0), sy = scatter.reduce((a, p) => a + p.y, 0);
    const sxx = scatter.reduce((a, p) => a + p.x * p.x, 0), sxy = scatter.reduce((a, p) => a + p.x * p.y, 0);
    const denom = n * sxx - sx * sx;
    if (!denom) return null;
    const slope = (n * sxy - sx * sy) / denom;
    return { slope, intercept: (sy - slope * sx) / n };
  }, [scatter]);

  const slowest = useMemo(() =>
    [...all.filter(r => r.trips >= minSlowTrips && r.avg_speed_kmph != null)]
      .sort((a, b) => (a.avg_speed_kmph ?? 0) - (b.avg_speed_kmph ?? 0)).slice(0, 10),
    [all, minSlowTrips]);

  const varianceLanes = useMemo(() => all.filter(r => r.trips >= minVarTrips && r.schedule_variance_hours != null), [all, minVarTrips]);
  const behind = useMemo(() => [...varianceLanes].sort((a, b) => (b.schedule_variance_hours ?? 0) - (a.schedule_variance_hours ?? 0)).slice(0, 10), [varianceLanes]);
  const ahead = useMemo(() => [...varianceLanes].sort((a, b) => (a.schedule_variance_hours ?? 0) - (b.schedule_variance_hours ?? 0)).slice(0, 10), [varianceLanes]);

  const treemap = useMemo(() =>
    all.slice(0, 40).map(r => ({ name: r.name, size: r.trips, color_value: r.otd_pct })),
    [all]);

  const paceNote = fit && fit.slope > 0
    ? `Fleet pace ≈ ${fit.slope.toFixed(3)} h/km (≈ ${(1 / fit.slope).toFixed(0)} km/h door-to-door)`
    : undefined;

  return (
    <PageContainer title="🛣️ Routes & Lanes">
      <div className="flex justify-end mb-4">
        <button onClick={() => downloadCsv(rows, 'lanes.csv')}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs text-gray-300 border border-gray-700 bg-gray-800 hover:text-white">
          <Download className="w-3.5 h-3.5" /> Export CSV
        </button>
      </div>

      <ChartCard title="🛤️ Lane Performance"
        method={{
          formula: "One row per origin→destination pair over the filtered trips.",
          plot: "table",
        }} icon={RouteIcon} iconColor="text-blue-400"
        explain={`A "lane" = plant → destination (min ${minLaneTrips} trip${minLaneTrips > 1 ? 's' : ''}). "Variance" is the honesty check on promised delivery windows: positive = actually slower than promised.`}>
        {loading ? <Spinner /> : (
          <div className="max-h-[420px] overflow-y-auto">
            <DataTable
              sortBy={sortBy} sortOrder={sortOrder}
              onSort={(c) => { if (sortBy === c) setSortOrder(o => o === 'asc' ? 'desc' : 'asc'); else { setSortBy(c); setSortOrder('desc'); } }}
              columns={[
                { key: 'name', label: 'Destination', sortable: true, render: (r: GroupRow) => <span className="text-gray-200 font-medium">{r.name}</span> },
                { key: 'trips', label: 'Trips', sortable: true },
                {
                  key: 'otd_pct', label: 'OTD %', sortable: true,
                  render: (r: GroupRow) => r.otd_pct != null
                    ? <span className={r.otd_pct >= 95 ? 'text-emerald-400 font-semibold' : r.otd_pct >= 80 ? 'text-amber-400' : 'text-red-400 font-semibold'}>{r.otd_pct}%</span> : '—',
                },
                { key: 'avg_transit_hours', label: 'Actual (h)', sortable: true },
                { key: 'avg_planned_transit_hours', label: 'Planned (h)', sortable: true },
                {
                  key: 'schedule_variance_hours', label: 'Variance', sortable: true,
                  render: (r: GroupRow) => r.schedule_variance_hours != null
                    ? <span className={r.schedule_variance_hours > 0 ? 'text-red-400 font-semibold' : 'text-emerald-400'}>
                        {r.schedule_variance_hours > 0 ? '+' : ''}{r.schedule_variance_hours} h</span> : '—',
                },
                { key: 'avg_distance_km', label: 'Avg km', sortable: true },
                { key: 'avg_speed_kmph', label: 'Speed', sortable: true },
                { key: 'avg_detention_hours', label: 'Detention (h)', sortable: true },
              ]}
              data={rows}
            />
          </div>
        )}
      </ChartCard>

      <ChartCard title="Distance vs Transit Time"
        method={{
          formula: "x = average distance, y = average transit hours, bubble area = trips.",
          plot: "bubble",
          caveat: "Points far above the general trend are lanes losing time that distance does not explain.",
        }} icon={MoveDiagonal} iconColor="text-cyan-400" className="mt-6"
        explain={`Bubbles well ABOVE the fitted line take longer than distance justifies (congestion, bad roads, slow carriers) — the best savings candidates.${paceNote ? ' ' + paceNote + '.' : ''}`}>
        <ScatterBubbleChart data={scatter} xLabel="Avg distance (km)" yLabel="Avg transit (h)"
          zLabel="Trips" cLabel="OTD %" trendLine={fit} height={380} />
      </ChartCard>

      <div className="grid lg:grid-cols-2 gap-6 mt-6">
        <ChartCard title="Slowest Corridors"
        method={{
          formula: "Mean transit hours, descending.",
          plot: "hbar",
          caveat: "Long lanes are legitimately slow. ‘Most behind plan’ below is the fairer ranking.",
        }} icon={TrendingDown} iconColor="text-red-400"
          explain="10 lowest average door-to-door speeds among lanes with ≥ 10 trips — very low km/h means excessive stopping and queuing, not slow driving.">
          <HBarChart data={slowest} nameKey="name" valueKey="avg_speed_kmph" valueLabel="km/h" color={tc(tc('#ef4444'))} />
        </ChartCard>
        <ChartCard title="Most Behind Plan"
        method={{
          formula: "Mean actual transit minus mean quoted transit, most positive first.",
          plot: "hbar",
        }} icon={Scale} iconColor="text-orange-400"
          explain="Largest positive schedule variance (≥ 5 trips) — renegotiate the delivery window or fix the route.">
          <HBarChart data={behind} nameKey="name" valueKey="schedule_variance_hours" valueLabel="Hours behind plan" color="#f97316" />
        </ChartCard>
      </div>

      <div className="grid lg:grid-cols-2 gap-6 mt-6">
        <ChartCard title="Most Ahead of Plan"
        method={{
          formula: "The same difference, most negative first.",
          plot: "hbar",
          caveat: "Consistently early is not free — it usually means the quoted ETA is padded, which costs planning accuracy elsewhere.",
        }} icon={Scale} iconColor="text-emerald-400"
          explain="Hidden buffer — lanes consistently ahead of promise; sales could quote these windows tighter.">
          <HBarChart data={ahead} nameKey="name" valueKey="schedule_variance_hours" valueLabel="Hours vs plan" color="#22c55e" />
        </ChartCard>
        <ChartCard title="Lane Volume Treemap"
        method={{
          formula: "Rectangle area is the lane's share of filtered trips.",
          plot: "table",
        }} icon={Grid3X3} iconColor="text-purple-400"
          explain="Top 40 lanes; tile size = trips, colour = OTD (red→green). Scan for LARGE RED tiles — that's the most revenue at risk.">
          <TreemapChart data={treemap} height={340} />
        </ChartCard>
      </div>
    </PageContainer>
  );
}
