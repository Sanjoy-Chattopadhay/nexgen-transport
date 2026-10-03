import { useState } from 'react';
import { BarChart3, LineChart as LineIcon, Box, Flag, Download } from 'lucide-react';
import { Link } from 'react-router-dom';
import PageContainer from '../../components/layout/PageContainer';
import Spinner from '../../components/ui/Spinner';
import ChartCard from '../../components/ui/ChartCard';
import DataTable from '../../components/ui/DataTable';
import HistogramChart from '../../components/charts/HistogramChart';
import ECDFChart from '../../components/charts/ECDFChart';
import BoxPlotChart from '../../components/charts/BoxPlotChart';
import { useApi } from '../../hooks/useApi';
import { useTTAFilters } from '../../components/tta/dashboard/FilterContext';
import { getDashBoxplot, getDashDistribution, getDashOutliers } from '../../services/ttaDashboard';
import { downloadCsv } from '../../lib/csv';

const METRICS = [
  { key: 'transit_hours', label: 'Transit hours', unit: 'h' },
  { key: 'distance_km', label: 'Distance (km)', unit: ' km' },
  { key: 'detention_hours', label: 'Detention hours', unit: 'h' },
  { key: 'delivery_delta_hours', label: 'Delivery delta (h, +late)', unit: 'h' },
  { key: 'avg_speed_kmph', label: 'Avg speed (km/h)', unit: ' km/h' },
  { key: 'plant_vivo_hours', label: 'Plant vivo hours', unit: 'h' },
  { key: 'dispatch_lead_hours', label: 'Dispatch lead hours', unit: 'h' },
  { key: 'gps_uptime', label: 'GPS uptime %', unit: '%' },
];

const GROUPS = [
  { key: 'transporter', label: 'Transporter' },
  { key: 'destination', label: 'Destination' },
  { key: 'vehicle_category', label: 'Vehicle category' },
  { key: 'own_market', label: 'Own / Market' },
  { key: 'consignor', label: 'Consignor' },
];

export default function Distributions() {
  const { params, paramsKey } = useTTAFilters();
  const [metric, setMetric] = useState('transit_hours');
  const [groupBy, setGroupBy] = useState('transporter');
  const [z, setZ] = useState(3.0);

  const { data: dist, loading } = useApi(() => getDashDistribution(metric, params), [paramsKey, metric]);
  const { data: box } = useApi(() => getDashBoxplot(groupBy, metric, 10, params), [paramsKey, metric, groupBy]);
  const { data: out } = useApi(() => getDashOutliers(z, params), [paramsKey, z]);

  const m = METRICS.find(x => x.key === metric)!;
  const stats = dist?.stats ?? {};

  return (
    <PageContainer title="📊 Distributions & Outliers">
      <div className="flex items-center gap-3 flex-wrap mb-6">
        <select value={metric} onChange={e => setMetric(e.target.value)}
          className="bg-gray-800 border border-gray-700 rounded-lg px-3 py-1.5 text-xs text-gray-300 outline-none">
          {METRICS.map(x => <option key={x.key} value={x.key}>{x.label}</option>)}
        </select>
      </div>

      {/* stats row */}
      {loading ? <Spinner /> : (
        <div className="grid grid-cols-4 md:grid-cols-8 gap-3 mb-6">
          {[
            ['Count', stats.count], ['Mean', stats.mean], ['Median', stats.median],
            ['Std dev', stats.std], ['P10', stats.p10], ['P90', stats.p90],
            ['P95', stats.p95], ['Skew', stats.skew],
          ].map(([label, v]) => (
            <div key={label as string} className="bg-gray-900 border border-gray-800 rounded-lg px-3 py-2">
              <p className="text-xs text-gray-500">{label}</p>
              <p className="text-sm font-bold text-gray-200">{v ?? '—'}</p>
            </div>
          ))}
        </div>
      )}
      <p className="text-xs text-gray-600 -mt-3 mb-5">
        Decoder: P95 is the number to base promises on — 95% of trips do better than it. High skew = a long tail of extreme trips that averages hide.
      </p>

      <div className="grid lg:grid-cols-2 gap-6 mb-6">
        <ChartCard title={`Histogram — ${m.label}`}
        method={{
          formula: "Frequency of the selected metric across the filtered trips. Bin count is derived from the row count so a wider window gives finer bars, not taller ones; mean and median are drawn as reference lines.",
          plot: "histogram",
          caveat: "Where mean and median are far apart the distribution is skewed, and the mean is the misleading one.",
        }} icon={BarChart3} iconColor="text-blue-400"
          explain="The long right tail hides in summary reports; the anomaly table below names those trips.">
          {dist?.histogram?.length
            ? <HistogramChart bins={dist.histogram} mean={stats.mean} median={stats.median} />
            : <p className="text-gray-500 text-sm py-6">No data for this metric</p>}
        </ChartCard>
        <ChartCard title="Cumulative Distribution (ECDF)"
        method={{
          formula: "For each value, the share of trips at or below it. Downsampled to at most 300 points for drawing; the percentiles quoted are computed on the full set.",
          plot: "ecdf",
        }} icon={LineIcon} iconColor="text-cyan-400"
          explain="The promise-setting chart — read the value where the curve crosses 95%.">
          {dist?.ecdf?.length
            ? <ECDFChart points={dist.ecdf} p95={stats.p95} unit={m.unit} />
            : <p className="text-gray-500 text-sm py-6">No data for this metric</p>}
        </ChartCard>
      </div>

      <ChartCard title={`Spread by ${GROUPS.find(g => g.key === groupBy)?.label}`}
        method={{
          formula: "Quartiles of the selected metric within each group, top groups by volume. Whiskers reach 1.5x the interquartile range; points beyond are drawn individually.",
          plot: "box",
        }} icon={Box} iconColor="text-purple-400"
        explain="Compare box heights, not just centres — predictability is worth almost as much as speed."
        actions={
          <select value={groupBy} onChange={e => setGroupBy(e.target.value)}
            className="bg-gray-800 border border-gray-700 rounded-lg px-2 py-1.5 text-xs text-gray-300 outline-none">
            {GROUPS.map(g => <option key={g.key} value={g.key}>{g.label}</option>)}
          </select>
        }>
        <BoxPlotChart groups={box?.groups ?? []} unit={m.unit} />
      </ChartCard>

      <ChartCard title="🚩 Automated Anomaly Detection"
        method={{
          formula: "Per-lane z-score: (this trip's transit − the lane's mean) ÷ the lane's standard deviation, over lanes with enough trips for a mean to mean anything. Rows past the threshold are listed worst-first.",
          plot: "table",
          caveat: "A z-score assumes a roughly symmetric spread. Transit times have a long right tail, so this over-flags slow trips relative to fast ones.",
        }} icon={Flag} iconColor="text-red-400" className="mt-6"
        explain="Per-lane z-score outliers on transit time (lanes with ≥ 8 trips). Each row is a question for the transporter: breakdown, diversion, driver issue, or bad data?"
        actions={
          <div className="flex items-center gap-3">
            <label className="flex items-center gap-2 text-xs text-gray-400">
              z ≥
              <input type="range" min={1.5} max={4} step={0.25} value={z}
                onChange={e => setZ(Number(e.target.value))} className="accent-blue-600" />
              <span className="text-blue-400 font-semibold w-8">{z.toFixed(2)}</span>
            </label>
            <button onClick={() => downloadCsv(out?.rows ?? [], 'outliers.csv')}
              className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs text-gray-300 border border-gray-700 bg-gray-800 hover:text-white">
              <Download className="w-3.5 h-3.5" /> CSV
            </button>
          </div>
        }>
        <div className="max-h-[420px] overflow-y-auto">
          <DataTable columns={[
            {
              key: 'trip_id', label: 'Trip',
              render: (r: any) => <Link to={`/trips/${r.trip_id}`} className="text-blue-400 hover:underline">{r.trip_id}</Link>,
            },
            { key: 'dept_dt', label: 'Departed' },
            { key: 'transporter', label: 'Transporter' },
            { key: 'vehicle_no', label: 'Vehicle', render: (r: any) => <span className="font-mono text-xs">{r.vehicle_no}</span> },
            { key: 'destination', label: 'Destination' },
            { key: 'transit_hours', label: 'Transit (h)', render: (r: any) => <span className="text-red-400 font-semibold">{r.transit_hours}</span> },
            { key: 'lane_mean_transit', label: 'Lane mean (h)' },
            {
              key: 'z_score', label: 'z',
              render: (r: any) => <span className={Math.abs(r.z_score) >= 4 ? 'text-red-400 font-bold' : 'text-amber-400 font-semibold'}>{r.z_score}</span>,
            },
            { key: 'delivery_status', label: 'Status' },
            { key: 'driver_name', label: 'Driver' },
          ]} data={out?.rows ?? []}
            emptyMessage="No anomalies at this threshold (needs lanes with ≥ 8 trips)" />
        </div>
      </ChartCard>
    </PageContainer>
  );
}
