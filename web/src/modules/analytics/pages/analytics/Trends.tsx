import { useMemo, useState } from 'react';
import {
  TrendingUp, ShieldCheck, Timer, Send, Route as RouteIcon, AlertTriangle, Download,
} from 'lucide-react';
import PageContainer from '../../components/layout/PageContainer';
import Spinner from '../../components/ui/Spinner';
import ChartCard from '../../components/ui/ChartCard';
import DualAxisChart from '../../components/charts/DualAxisChart';
import { useApi } from '../../hooks/useApi';
import { useTTAFilters } from '../../components/tta/dashboard/FilterContext';
import { getDashTimeseries } from '../../services/ttaDashboard';
import { downloadCsv } from '../../lib/csv';
import { tc } from '../../../../core/theme';

const GRANULARITIES = [
  { key: 'D' as const, label: 'Daily' },
  { key: 'W' as const, label: 'Weekly' },
  { key: 'M' as const, label: 'Monthly' },
];

export default function Trends() {
  const { params, paramsKey } = useTTAFilters();
  const [gran, setGran] = useState<'D' | 'W' | 'M'>('D');

  const { data, loading } = useApi(() => getDashTimeseries(gran, params), [paramsKey, gran]);
  const series = data?.series ?? [];

  // rolling 3-period mean of volume
  const withMa = useMemo(() => series.map((r: any, i: number) => {
    const win = series.slice(Math.max(0, i - 2), i + 1);
    return { ...r, ma3: win.reduce((a: number, x: any) => a + x.trips, 0) / win.length };
  }), [series]);

  const label = GRANULARITIES.find(g => g.key === gran)!.label;

  return (
    <PageContainer title="📈 Time & Trends">
      <div className="flex items-center justify-between flex-wrap gap-3 mb-6">
        <div className="flex rounded-lg overflow-hidden border border-gray-700">
          {GRANULARITIES.map(g => (
            <button key={g.key} onClick={() => setGran(g.key)}
              className={`px-4 py-1.5 text-xs font-medium transition-colors ${
                gran === g.key ? 'bg-blue-600 text-white' : 'bg-gray-800 text-gray-400 hover:text-gray-200'}`}>
              {g.label}
            </button>
          ))}
        </div>
        <button onClick={() => downloadCsv(series, 'trends.csv')}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs text-gray-300 border border-gray-700 bg-gray-800 hover:text-white">
          <Download className="w-3.5 h-3.5" /> Export CSV
        </button>
      </div>

      {loading ? <Spinner /> : (
        <div className="grid lg:grid-cols-2 gap-6">
          <ChartCard title={`${label} Trip Volume`} icon={TrendingUp} iconColor="text-cyan-400"
            method={{
              formula: "Trips counted by the period their departure timestamp falls in.",
              plot: "line",
              caveat: "The first and last period are usually partial, so a drop at either end is the window, not a trend.",
            }}
            explain="Dispatch pulse with 3-period rolling mean — compare against production plans.">
            <DualAxisChart data={withMa} xKey="period" height={260} series={[
              { key: 'trips', label: 'Trips', color: tc('#06b6d4'), type: 'bar' },
              { key: 'ma3', label: '3-period avg', color: '#ffd166', dashed: true },
            ]} />
          </ChartCard>

          <ChartCard title="On-time Delivery % Trend"
        method={{
          formula: "On-time trips ÷ judged trips, per period.",
          plot: "line",
        }} icon={ShieldCheck} iconColor="text-emerald-400"
            explain="A slow drift downward is a structural problem, not bad luck. Red line = 95% target.">
            <DualAxisChart data={series} xKey="period" height={260}
              refLineLeft={{ y: 95, label: 'target 95%' }}
              series={[{ key: 'otd_pct', label: 'OTD %', color: '#22c55e' }]} />
          </ChartCard>

          <ChartCard title="Transit & Detention Hours"
        method={{
          formula: "Mean transit and mean detention hours per period.",
          plot: "line",
        }} icon={Timer} iconColor="text-orange-400"
            explain="Rising red = customers holding trucks at their gates — pure waste that shrinks effective fleet capacity.">
            <DualAxisChart data={series} xKey="period" height={260} series={[
              { key: 'avg_transit_hours', label: 'Avg transit (h)', color: tc('#f59e0b'), type: 'area' },
              { key: 'avg_detention_hours', label: 'Avg detention (h)', color: tc('#ef4444') },
            ]} />
          </ChartCard>

          <ChartCard title="Dispatch Lead Time"
        method={{
          formula: "Mean hours from booking to departure, per period.",
          plot: "line",
          caveat: "Negative lead times are dropped as bad stamps rather than clamped to zero.",
        }} icon={Send} iconColor="text-purple-400"
            explain="Internal friction between booking a truck and it leaving the gate — shrinking it is free speed.">
            <DualAxisChart data={series} xKey="period" height={260} series={[
              { key: 'avg_dispatch_lead_hours', label: 'Avg dispatch lead (h)', color: '#8b5cf6' },
            ]} />
          </ChartCard>

          <ChartCard title="Distance Covered"
        method={{
          formula: "Sum of trip distance per period.",
          plot: "line",
        }} icon={RouteIcon} iconColor="text-blue-400"
            explain="Freight-spend / fuel proxy; km growing faster than trips means longer average hauls.">
            <DualAxisChart data={series} xKey="period" height={260} series={[
              { key: 'total_km', label: 'Total km', color: tc('#3b82f6'), type: 'area' },
            ]} />
          </ChartCard>

          <ChartCard title="Speed Violations"
        method={{
          formula: "The provider's own violation count summed per period.",
          plot: "line",
          caveat: "This is the TMS field, not the platform's own episode detection — see Speed & Safety for episodes computed against a limit you choose.",
        }} icon={AlertTriangle} iconColor="text-red-400"
            explain="Safety exposure spikes (telemetry events, hence large numbers) — the Fleet page names the vehicles.">
            <DualAxisChart data={series} xKey="period" height={260} series={[
              { key: 'speed_violations', label: 'Speed violations', color: tc('#ef4444'), type: 'bar' },
            ]} />
          </ChartCard>
        </div>
      )}
    </PageContainer>
  );
}
