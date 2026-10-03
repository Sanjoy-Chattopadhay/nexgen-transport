import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { MapPin, Users, Truck, Gauge, Target, TrendingUp, Clock, ShieldCheck, AlertTriangle, Brain, Sparkles, ArrowRight, Search, BarChart3 } from 'lucide-react';
import PageContainer from '../components/layout/PageContainer';
import KPICard from '../components/ui/KPICard';
import { ProofGrid } from '../../../core/proof/ProofPanel';
import DateRangeFilter from '../components/ui/DateRangeFilter';
import AreaChart from '../components/charts/AreaChart';
import Spinner from '../components/ui/Spinner';
import { useApi } from '../hooks/useApi';
import { useDateRange } from '../hooks/useDateRange';
import { useDrillDown } from '../context/DrillDownContext';
import { getFleetSummary, getDailyTrend, getActiveDrivers, getActiveVehicles, getRecentTrips } from '../services/dashboard';
import { predictEta, recommendDrivers } from '../services/ml';
import { CHART_COLORS } from '../lib/colors';
import { KPI_INFO } from '../lib/kpiInfo';
import { formatNumber, formatDistance, formatSpeed, formatPercent, formatDateTime } from '../lib/formatters';
import type { FleetSummary, DailyTrend } from '../types/dashboard';

/**
 * The three places you actually go from here.
 *
 * This slot used to hold a "Top Drivers" ranking. It was the wrong thing on the
 * landing page: a leaderboard of the ten drivers with the best ETA rate is a
 * vanity list — nobody acts on it, the names at the top are usually whoever
 * drove the easiest routes, and it crowded out the routes into the sections
 * that do carry decisions.
 */
const SECTION_SHORTCUTS = [
  {
    label: 'TTA Analytics', path: '/analytics', icon: BarChart3,
    desc: 'Service, lanes, speed and safety across every trip in the window.',
    color: 'text-blue-400', bg: 'bg-blue-500/10', border: 'border-blue-500/20',
  },
  {
    label: 'Transporters', path: '/transporters', icon: Truck,
    desc: 'Carrier league table, the volume-vs-reliability matrix, and a full profile each.',
    color: 'text-purple-400', bg: 'bg-purple-500/10', border: 'border-purple-500/20',
  },
  {
    label: 'ML Insights', path: '/ml', icon: Brain,
    desc: 'ETA and SLA prediction, anomaly scanning, demand forecasting.',
    color: 'text-emerald-400', bg: 'bg-emerald-500/10', border: 'border-emerald-500/20',
  },
];

const ML_SHORTCUTS = [
  { label: 'ETA Predictor', desc: 'Predict trip duration', path: '/ml/eta', icon: Clock, color: 'text-blue-400', bg: 'bg-blue-500/10', border: 'border-blue-500/20' },
  { label: 'SLA Risk', desc: 'Check delivery risk', path: '/ml/sla', icon: ShieldCheck, color: 'text-emerald-400', bg: 'bg-emerald-500/10', border: 'border-emerald-500/20' },
  { label: 'Anomaly Scan', desc: 'Detect trip anomalies', path: '/ml/anomaly', icon: AlertTriangle, color: 'text-amber-400', bg: 'bg-amber-500/10', border: 'border-amber-500/20' },
  { label: 'Fatigue Monitor', desc: 'Driver safety check', path: '/ml/fatigue', icon: Brain, color: 'text-red-400', bg: 'bg-red-500/10', border: 'border-red-500/20' },
  { label: 'Recommender', desc: 'Best driver for route', path: '/ml/recommender', icon: Users, color: 'text-cyan-400', bg: 'bg-cyan-500/10', border: 'border-cyan-500/20' },
  { label: 'Demand Forecast', desc: '7-day trip forecast', path: '/ml/demand', icon: TrendingUp, color: 'text-indigo-400', bg: 'bg-indigo-500/10', border: 'border-indigo-500/20' },
];

function fmtDuration(minutes: number): string {
  if (minutes == null) return '-';
  const d = Math.floor(minutes / 1440);
  const h = Math.floor((minutes % 1440) / 60);
  const m = Math.round(minutes % 60);
  const parts: string[] = [];
  if (d > 0) parts.push(`${d}d`);
  if (h > 0) parts.push(`${h}h`);
  if (m > 0 || parts.length === 0) parts.push(`${m}m`);
  return parts.join(' ');
}

function QuickQuery() {
  const [mode, setMode] = useState<'eta' | 'recommend'>('eta');
  const [origin, setOrigin] = useState('');
  const [destination, setDestination] = useState('');
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<any>(null);
  const [error, setError] = useState('');
  const navigate = useNavigate();

  const canQuery = origin.trim() && destination.trim();

  const handleQuery = async () => {
    if (!canQuery) return;
    setLoading(true); setResult(null); setError('');
    try {
      if (mode === 'eta') {
        const res = await predictEta({ origin: origin.trim(), destination: destination.trim(), trip_start: new Date().toISOString() });
        setResult({ type: 'eta', data: res.data });
      } else {
        const res = await recommendDrivers({ origin: origin.trim(), destination: destination.trim(), top_n: 3 });
        setResult({ type: 'recommend', data: res.data });
      }
    } catch (err: any) {
      setError(err?.response?.data?.detail || err.message || 'Query failed');
    }
    setLoading(false);
  };

  return (
    <div>
      {/* Mode Tabs */}
      <div className="flex gap-1 mb-3">
        <button onClick={() => { setMode('eta'); setResult(null); }}
          className={`flex-1 py-1.5 rounded-lg text-xs font-medium transition-colors ${mode === 'eta' ? 'bg-blue-600/20 text-blue-400 border border-blue-500/30' : 'text-gray-500 hover:text-gray-300'}`}>
          <Clock className="w-3 h-3 inline mr-1" />ETA
        </button>
        <button onClick={() => { setMode('recommend'); setResult(null); }}
          className={`flex-1 py-1.5 rounded-lg text-xs font-medium transition-colors ${mode === 'recommend' ? 'bg-cyan-600/20 text-cyan-400 border border-cyan-500/30' : 'text-gray-500 hover:text-gray-300'}`}>
          <Users className="w-3 h-3 inline mr-1" />Recommend
        </button>
      </div>

      {/* Inputs */}
      <div className="space-y-2 mb-3">
        <input type="text" value={origin} onChange={e => setOrigin(e.target.value)} placeholder="Origin (e.g. JHARSUGUDA)"
          className="w-full px-3 py-2 bg-gray-800 border border-gray-700 rounded-lg text-sm text-white placeholder-gray-600 focus:outline-none focus:border-blue-500/50 transition-all" />
        <input type="text" value={destination} onChange={e => setDestination(e.target.value)} placeholder="Destination (e.g. CHENNAI)"
          className="w-full px-3 py-2 bg-gray-800 border border-gray-700 rounded-lg text-sm text-white placeholder-gray-600 focus:outline-none focus:border-blue-500/50 transition-all" />
      </div>

      <button onClick={handleQuery} disabled={!canQuery || loading}
        className={`w-full py-2 rounded-lg text-sm font-medium transition-all flex items-center justify-center gap-2 ${
          canQuery ? 'bg-blue-600 hover:bg-blue-500 text-white' : 'bg-gray-800 text-gray-500 cursor-not-allowed'
        }`}>
        {loading ? <div className="w-3.5 h-3.5 border-2 border-white/30 border-t-white rounded-full animate-spin" />
          : <Sparkles className="w-3.5 h-3.5" />}
        {mode === 'eta' ? 'Predict ETA' : 'Find Drivers'}
      </button>

      {/* Result */}
      {error && <p className="text-xs text-red-400 mt-2">{error}</p>}

      {result?.type === 'eta' && result.data?.predicted_duration_minutes != null && (
        <div className="mt-3 bg-blue-900/20 rounded-lg border border-blue-800/30 p-3 animate-scale-in">
          <div className="flex items-center justify-between mb-1">
            <span className="text-xs text-gray-400">Predicted Duration</span>
            <span className="text-lg font-bold text-blue-400">{fmtDuration(result.data.predicted_duration_minutes)}</span>
          </div>
          {result.data.route_avg_duration != null && (
            <div className="flex items-center justify-between text-xs">
              <span className="text-gray-500">Route Average</span>
              <span className="text-gray-400">{fmtDuration(result.data.route_avg_duration)}</span>
            </div>
          )}
          <button onClick={() => navigate('/ml/eta')} className="text-xs text-blue-400 hover:text-blue-300 mt-2 flex items-center gap-1">
            Full prediction <ArrowRight className="w-3 h-3" />
          </button>
        </div>
      )}

      {result?.type === 'recommend' && (
        <div className="mt-3 space-y-1.5 animate-scale-in">
          {(result.data?.experienced_on_route || []).slice(0, 3).map((d: any) => (
            <div key={d.driver_id} onClick={() => navigate(`/drivers/${d.driver_id}`)}
              className="flex items-center justify-between bg-cyan-900/15 rounded-lg border border-cyan-800/25 px-3 py-2 cursor-pointer hover:bg-cyan-900/25 transition-colors">
              <div>
                <p className="text-sm text-white">{d.driver_name}</p>
                <p className="text-xs text-gray-500">{d.route_trips} trips on route | ETA: {d.eta_success_rate?.toFixed(0)}%</p>
              </div>
              <span className="text-sm font-bold text-cyan-400">{d.composite_score?.toFixed(0)}</span>
            </div>
          ))}
          {(result.data?.experienced_on_route || []).length === 0 && (
            <p className="text-xs text-gray-500 text-center py-2">No experienced drivers for this route</p>
          )}
          <button onClick={() => navigate('/ml/recommender')} className="text-xs text-cyan-400 hover:text-cyan-300 flex items-center gap-1">
            Full recommendations <ArrowRight className="w-3 h-3" />
          </button>
        </div>
      )}
    </div>
  );
}

export default function Dashboard() {
  const navigate = useNavigate();
  const { open } = useDrillDown();
  const { from, to, preset, setPreset, setCustom } = useDateRange();

  const { data: summary, loading: sLoad, refetch: rSummary } = useApi<FleetSummary>(() => getFleetSummary(from, to), [from, to]);
  const { data: trend, refetch: rTrend } = useApi<DailyTrend[]>(() => getDailyTrend(90, from, to), [from, to]);

  // Auto-refresh every 5 minutes so the server-cached numbers refresh on their
  // own — the dashboard "updates regularly" without a manual reload.
  useEffect(() => {
    const id = setInterval(() => { rSummary(); rTrend(); }, 5 * 60 * 1000);
    return () => clearInterval(id);
  }, [rSummary, rTrend]);

  const rangeLabel = preset === 'all' ? 'all time' : `${from} → ${to}`;

  const drillTrips = () => open({
    title: 'Trips in range',
    subtitle: rangeLabel,
    columns: [
      { key: 'dispatch_entry_no', label: 'Trip' },
      { key: 'route', label: 'Route', render: (r: any) => `${r.origin ?? '—'} → ${r.destination ?? '—'}` },
      { key: 'driver_name', label: 'Driver' },
      { key: 'trip_start', label: 'Departed', render: (r: any) => formatDateTime(r.trip_start) },
      { key: 'eta_met', label: 'ETA', align: 'right', render: (r: any) => r.eta_met == null ? '—' : r.eta_met ? '✓' : '✗' },
    ],
    rowLink: (r: any) => `/trips/${r.dispatch_entry_no}`,
    load: () => getRecentTrips(from, to).then(res => res.data),
    empty: 'No trips in this range.',
  });

  const drillDrivers = () => open({
    title: 'Active drivers',
    subtitle: `Trips in ${rangeLabel}`,
    columns: [
      { key: 'driver_name', label: 'Driver' },
      { key: 'total_trips', label: 'Trips', align: 'right' },
      { key: 'eta_success_rate', label: 'ETA %', align: 'right', render: (r: any) => r.eta_success_rate != null ? `${r.eta_success_rate}%` : '—' },
      { key: 'total_distance_km', label: 'Distance', align: 'right', render: (r: any) => formatDistance(r.total_distance_km) },
    ],
    rowLink: (r: any) => `/drivers/${r.driver_id}`,
    load: () => getActiveDrivers(from, to).then(res => res.data),
    empty: 'No drivers ran a trip in this range.',
  });

  const drillVehicles = () => open({
    title: 'Active vehicles',
    subtitle: `Trips in ${rangeLabel}`,
    columns: [
      { key: 'asset_id', label: 'Vehicle' },
      { key: 'asset_type', label: 'Type' },
      { key: 'total_trips', label: 'Trips', align: 'right' },
      { key: 'total_distance_km', label: 'Distance', align: 'right', render: (r: any) => formatDistance(r.total_distance_km) },
    ],
    rowLink: (r: any) => `/vehicles/${r.vehicle_id}`,
    load: () => getActiveVehicles(from, to).then(res => res.data),
    empty: 'No vehicles ran a trip in this range.',
  });

  const proofParams = { date_from: from, date_to: to };

  const etaColor = summary ? (summary.eta_success_rate != null && summary.eta_success_rate >= 90 ? 'green' : summary.eta_success_rate != null && summary.eta_success_rate >= 80 ? 'amber' : 'red') : 'red';

  return (
    <PageContainer>
      <DateRangeFilter from={from} to={to} preset={preset} onPreset={setPreset} onCustom={setCustom}
        note={summary ? `${formatNumber(summary.total_trips)} trips in range` : undefined} />

      {sLoad ? <Spinner /> : summary && (
        // Every tile opens to how it is calculated and the records behind it.
        <ProofGrid className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-4 mb-6">
          <KPICard label="Total Trips" value={formatNumber(summary.total_trips)} icon={MapPin} color="blue"
            info={KPI_INFO.totalTrips} onDrill={drillTrips} drillLabel="See trips in range"
            proof={{ dataset: 'dashboard.trips', params: proofParams, value: summary.total_trips }} />
          <KPICard label="Active Drivers" value={formatNumber(summary.total_drivers)} icon={Users} color="green"
            info={KPI_INFO.activeDrivers} onDrill={drillDrivers} drillLabel="See active drivers"
            proof={{ dataset: 'dashboard.drivers', params: proofParams, value: summary.total_drivers }} />
          <KPICard label="Vehicles" value={formatNumber(summary.total_vehicles)} icon={Truck} color="amber"
            info={KPI_INFO.vehicles} onDrill={drillVehicles} drillLabel="See active vehicles"
            proof={{ dataset: 'dashboard.vehicles', params: proofParams, value: summary.total_vehicles }} />
          <KPICard label="Total Distance" value={formatDistance(summary.total_distance_km)} icon={Gauge} color="purple"
            info={KPI_INFO.totalDistance}
            proof={{ dataset: 'dashboard.distance', params: proofParams, value: summary.total_distance_km }} />
          <KPICard label="Avg Speed" value={formatSpeed(summary.avg_speed_kmph)} icon={TrendingUp} color="cyan"
            info={KPI_INFO.avgSpeed}
            proof={{ dataset: 'dashboard.speed', params: proofParams, value: summary.avg_speed_kmph }} />
          <KPICard label="ETA Success Rate" value={formatPercent(summary.eta_success_rate)} icon={Target} color={etaColor}
            info={KPI_INFO.etaSuccess}
            proof={{ dataset: 'dashboard.eta', params: proofParams, value: summary.eta_success_rate }} />
        </ProofGrid>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-6">
        <div className="bg-gray-900 rounded-xl border border-gray-800 p-5">
          <h2 className="text-lg font-semibold text-white mb-4">Daily Trip Trend</h2>
          {trend ? (
            <AreaChart data={trend} xKey="stat_date" series={[{ key: 'total_trips', color: CHART_COLORS.primary, label: 'Trips' }]} height={280} />
          ) : <Spinner />}
        </div>
        <div className="bg-gray-900 rounded-xl border border-gray-800 p-5">
          <h2 className="text-lg font-semibold text-white mb-4">ETA Success Rate Trend</h2>
          {trend ? (
            <AreaChart data={trend} xKey="stat_date" series={[{ key: 'eta_success_rate', color: CHART_COLORS.secondary, label: 'ETA Rate %' }]} height={280} />
          ) : <Spinner />}
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <div className="bg-gray-900 rounded-xl border border-gray-800 p-5">
          <h2 className="text-lg font-semibold text-white mb-1">Where to go next</h2>
          <p className="text-xs text-gray-500 mb-4">
            The three sections that carry decisions. Everything else is reachable from the sidebar.
          </p>
          <div className="space-y-2.5">
            {SECTION_SHORTCUTS.map(sc => (
              <button key={sc.path} onClick={() => navigate(sc.path)}
                className={`w-full ${sc.bg} border ${sc.border} rounded-lg p-3.5 text-left flex items-start gap-3 hover:scale-[1.01] transition-all group`}>
                <sc.icon className={`w-5 h-5 ${sc.color} shrink-0 mt-0.5`} />
                <div className="min-w-0">
                  <p className="text-sm font-semibold text-white flex items-center gap-1.5">
                    {sc.label}
                    <ArrowRight className="w-3.5 h-3.5 opacity-0 -translate-x-1 transition-all group-hover:opacity-60 group-hover:translate-x-0" />
                  </p>
                  <p className="text-xs text-gray-400 mt-0.5 leading-relaxed">{sc.desc}</p>
                </div>
              </button>
            ))}
          </div>
        </div>

        {/* ML Quick Actions + Query */}
        <div className="bg-gray-900 rounded-xl border border-gray-800 p-5">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-lg font-semibold text-white flex items-center gap-2">
              <Sparkles className="w-5 h-5 text-blue-400" /> ML Quick Actions
            </h2>
            <button onClick={() => navigate('/ml')} className="text-xs text-blue-400 hover:text-blue-300 flex items-center gap-1">
              All Models <ArrowRight className="w-3 h-3" />
            </button>
          </div>

          {/* Shortcut Grid */}
          <div className="grid grid-cols-3 gap-2 mb-5">
            {ML_SHORTCUTS.map(s => (
              <button key={s.path} onClick={() => navigate(s.path)}
                className={`${s.bg} border ${s.border} rounded-lg p-2.5 text-left hover:scale-[1.03] transition-all`}>
                <s.icon className={`w-4 h-4 ${s.color} mb-1`} />
                <p className="text-xs font-medium text-white">{s.label}</p>
                <p className="text-xs text-gray-500">{s.desc}</p>
              </button>
            ))}
          </div>

          {/* Inline Quick Query */}
          <div className="border-t border-gray-800 pt-4">
            <h3 className="text-sm font-medium text-gray-300 mb-3 flex items-center gap-2">
              <Search className="w-4 h-4 text-gray-500" /> Quick Query
            </h3>
            <QuickQuery />
          </div>
        </div>
      </div>
    </PageContainer>
  );
}
