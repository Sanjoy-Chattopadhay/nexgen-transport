import { useState, useEffect, useCallback } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { ArrowLeft, MapPin, Target, Gauge, Truck, Zap, Clock, TrendingUp, Award, ShieldAlert, AlertTriangle, Moon, OctagonAlert, Activity, ShieldCheck } from 'lucide-react';
import { ResponsiveContainer, BarChart as RechartsBar, Bar, XAxis, YAxis, CartesianGrid, Tooltip, AreaChart as RechartsArea, Area, Cell, RadarChart, PolarGrid, PolarAngleAxis, PolarRadiusAxis, Radar, ComposedChart, Line, PieChart, Pie } from 'recharts';
import PageContainer from '../components/layout/PageContainer';
import KPICard from '../components/ui/KPICard';
import { ProofGrid } from '../../../core/proof/ProofPanel';
import Badge from '../components/ui/Badge';
import Spinner from '../components/ui/Spinner';
import { useApi } from '../hooks/useApi';
import { getDriverDetail, getDriverTrend, getDriverDrivingPattern, getDriverAlerts } from '../services/drivers';
import { getDriverScore } from '../services/ml';
import { CHART_COLORS } from '../lib/colors';
import { formatNumber, formatPercent, formatSpeed, formatDuration, formatDistance, formatDateTime, rateKpiColor } from '../lib/formatters';
import type { DriverDetail as DriverDetailType, DriverTrend, DrivingPattern, DriverAlerts } from '../types/driver';
import { tc } from '../../../core/theme';

const TOOLTIP_STYLE = { backgroundColor: CHART_COLORS.tooltipBg, border: `1px solid ${tc('#374151')}`, borderRadius: 8, color: tc('#f3f4f6') };
const PIE_COLORS = [tc('#ef4444'), tc('#f59e0b'), tc('#10b981'), tc('#3b82f6'), '#8b5cf6', tc('#06b6d4')];

const WINDOWS: { key: string; label: string }[] = [
  { key: 'all', label: 'All time' },
  { key: '30d', label: 'Last 30 days' },
  { key: '15d', label: 'Last 15 days' },
  { key: '7d', label: 'Last 7 days' },
  { key: 'last10', label: 'Last 10 trips' },
  { key: 'last5', label: 'Last 5 trips' },
  { key: 'last3', label: 'Last 3 trips' },
];

const SEV_META: Record<string, { color: string; bg: string; icon: any; label: string }> = {
  critical: { color: tc('#ef4444'), bg: 'bg-red-950/40 border-red-800/50', icon: OctagonAlert, label: 'Critical' },
  warning: { color: tc('#f59e0b'), bg: 'bg-amber-950/40 border-amber-800/50', icon: AlertTriangle, label: 'Warning' },
  info: { color: tc('#3b82f6'), bg: 'bg-blue-950/40 border-blue-800/50', icon: Activity, label: 'Info' },
};

const TYPE_META: Record<string, { icon: any; label: string; color: string }> = {
  overspeed: { icon: Gauge, label: 'Overspeed', color: 'text-red-400' },
  harsh_driving: { icon: Zap, label: 'Harsh driving', color: 'text-amber-400' },
  long_stop: { icon: Clock, label: 'Long stops', color: 'text-blue-400' },
  night_driving: { icon: Moon, label: 'Night driving', color: 'text-purple-400' },
};

function getRankLabel(score: number) {
  if (score >= 80) return { label: 'Platinum', color: 'text-cyan-300', bg: 'from-cyan-900/40 to-cyan-950/60', ring: 'ring-cyan-500/30' };
  if (score >= 60) return { label: 'Gold', color: 'text-amber-300', bg: 'from-amber-900/40 to-amber-950/60', ring: 'ring-amber-500/30' };
  if (score >= 40) return { label: 'Silver', color: 'text-gray-300', bg: 'from-gray-700/40 to-gray-900/60', ring: 'ring-gray-500/30' };
  return { label: 'Bronze', color: 'text-orange-400', bg: 'from-orange-900/40 to-orange-950/60', ring: 'ring-orange-500/30' };
}

function ScoreRing({ score }: { score: number }) {
  const r = 54, c = 2 * Math.PI * r;
  const offset = c - (score / 100) * c;
  const color = score >= 70 ? tc('#10b981') : score >= 50 ? tc('#f59e0b') : tc('#ef4444');
  return (
    <svg width="130" height="130" viewBox="0 0 130 130">
      <circle cx="65" cy="65" r={r} fill="none" stroke={tc(tc('#1f2937'))} strokeWidth="10" />
      <circle cx="65" cy="65" r={r} fill="none" stroke={color} strokeWidth="10"
        strokeDasharray={c} strokeDashoffset={offset} strokeLinecap="round"
        transform="rotate(-90 65 65)" className="transition-all duration-1000" />
      <text x="65" y="60" textAnchor="middle" fill="white" fontSize="28" fontWeight="bold">{score.toFixed(0)}</text>
      <text x="65" y="80" textAnchor="middle" fill={tc(tc('#9ca3af'))} fontSize="12">/100</text>
    </svg>
  );
}

export default function DriverDetail() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const driverId = Number(id);
  const num = (v: unknown) => (v == null || v === '' ? null : Number(v));

  // Date filter state
  const [dateFrom, setDateFrom] = useState('');
  const [dateTo, setDateTo] = useState('');
  const [groupBy, setGroupBy] = useState<'month' | 'week' | 'day'>('month');

  const { data, loading } = useApi<DriverDetailType>(() => getDriverDetail(driverId), [driverId]);

  // Trend with date filtering (manual fetch)
  const [trend, setTrend] = useState<DriverTrend[] | null>(null);
  const [trendLoading, setTrendLoading] = useState(false);

  const fetchTrend = useCallback(() => {
    setTrendLoading(true);
    const params: any = { group_by: groupBy };
    if (dateFrom) params.date_from = dateFrom;
    if (dateTo) params.date_to = dateTo;
    getDriverTrend(driverId, params)
      .then(res => setTrend(res.data))
      .catch(() => setTrend([]))
      .finally(() => setTrendLoading(false));
  }, [driverId, dateFrom, dateTo, groupBy]);

  useEffect(() => { fetchTrend(); }, [fetchTrend]);

  // Insights window (shared by driving-pattern + alerts)
  const [insightWindow, setInsightWindow] = useState('all');
  const [drivingPattern, setDrivingPattern] = useState<DrivingPattern | null>(null);
  const [alerts, setAlerts] = useState<DriverAlerts | null>(null);
  const [insightsLoading, setInsightsLoading] = useState(false);

  useEffect(() => {
    setInsightsLoading(true);
    Promise.all([
      getDriverDrivingPattern(driverId, insightWindow).then(r => setDrivingPattern(r.data)).catch(() => setDrivingPattern(null)),
      getDriverAlerts(driverId, insightWindow).then(r => setAlerts(r.data)).catch(() => setAlerts(null)),
    ]).finally(() => setInsightsLoading(false));
  }, [driverId, insightWindow]);

  // ML Score
  const [scoreData, setScoreData] = useState<any>(null);
  useEffect(() => {
    getDriverScore(driverId)
      .then(res => setScoreData(res.data))
      .catch(() => {});
  }, [driverId]);

  if (loading) return <Spinner />;
  if (!data) return <p className="text-gray-500">Driver not found</p>;

  const s = data.summary;
  const score = scoreData?.composite_score ?? null;
  const rank = score != null ? getRankLabel(score) : null;

  // Build radar data from score breakdown
  const radarData = scoreData?.scores ? [
    { metric: 'ETA Rate', value: scoreData.scores.eta_score ?? 0 },
    { metric: 'Speed', value: scoreData.scores.speed_score ?? 0 },
    { metric: 'Consistency', value: scoreData.scores.consistency_score ?? 0 },
    { metric: 'Experience', value: scoreData.scores.experience_score ?? 0 },
    { metric: 'Reliability', value: scoreData.scores.reliability_score ?? 0 },
  ].filter(d => d.value > 0) : [];

  return (
    <PageContainer title="">
      <button onClick={() => navigate('/drivers')} className="flex items-center gap-2 text-gray-400 hover:text-white mb-4 text-sm">
        <ArrowLeft className="w-4 h-4" /> Back to Drivers
      </button>

      {/* ===== GAMING-LIKE PROFILE CARD ===== */}
      <div className={`relative bg-gradient-to-r ${rank?.bg || 'from-gray-800/50 to-gray-900/60'} rounded-2xl border border-gray-700/50 ${rank?.ring || ''} ring-1 p-6 mb-6 overflow-hidden`}>
        <div className="absolute top-0 right-0 w-48 h-48 bg-gradient-to-bl from-white/5 to-transparent rounded-bl-full" />
        <div className="flex flex-col lg:flex-row items-start lg:items-center gap-6">
          {/* Avatar + Score Ring */}
          <div className="flex items-center gap-5">
            <div className="relative">
              <div className="w-20 h-20 rounded-full bg-gradient-to-br from-blue-600 to-purple-600 flex items-center justify-center text-3xl font-bold text-white shadow-lg shadow-blue-500/20">
                {s.driver_name?.charAt(0)?.toUpperCase() || 'D'}
              </div>
              {rank && (
                <div className={`absolute -bottom-1 -right-1 px-1.5 py-0.5 rounded-full text-xs font-bold ${rank.color} bg-gray-900 border border-gray-700`}>
                  {rank.label}
                </div>
              )}
            </div>
            {score != null && <ScoreRing score={score} />}
          </div>

          {/* Info */}
          <div className="flex-1 min-w-0">
            <h1 className="text-2xl font-bold text-white mb-1">{s.driver_name}</h1>
            <p className="text-gray-400 text-sm mb-3">{s.driver_mobile || 'No mobile'}</p>
            <div className="flex flex-wrap gap-3">
              <div className="flex items-center gap-1.5 bg-gray-800/60 rounded-lg px-3 py-1.5">
                <MapPin className="w-3.5 h-3.5 text-blue-400" />
                <span className="text-xs text-gray-300"><span className="font-semibold text-white">{formatNumber(s.total_trips)}</span> trips</span>
              </div>
              <div className="flex items-center gap-1.5 bg-gray-800/60 rounded-lg px-3 py-1.5">
                <Gauge className="w-3.5 h-3.5 text-cyan-400" />
                <span className="text-xs text-gray-300"><span className="font-semibold text-white">{formatSpeed(s.avg_speed_kmph)}</span></span>
              </div>
              <div className="flex items-center gap-1.5 bg-gray-800/60 rounded-lg px-3 py-1.5">
                <Target className="w-3.5 h-3.5 text-emerald-400" />
                <span className="text-xs text-gray-300">ETA <span className="font-semibold text-white">{formatPercent(s.eta_success_rate)}</span></span>
              </div>
              <div className="flex items-center gap-1.5 bg-gray-800/60 rounded-lg px-3 py-1.5">
                <Truck className="w-3.5 h-3.5 text-purple-400" />
                <span className="text-xs text-gray-300"><span className="font-semibold text-white">{formatDistance(s.total_distance_km)}</span></span>
              </div>
              <div className="flex items-center gap-1.5 bg-gray-800/60 rounded-lg px-3 py-1.5">
                <Clock className="w-3.5 h-3.5 text-amber-400" />
                <span className="text-xs text-gray-300">Avg <span className="font-semibold text-white">{formatDuration(s.avg_duration_min)}</span></span>
              </div>
            </div>
          </div>

          {/* Radar Chart (if score data available) */}
          {radarData.length > 0 && (
            <div className="hidden xl:block flex-shrink-0">
              <ResponsiveContainer width={200} height={180}>
                <RadarChart data={radarData}>
                  <PolarGrid stroke={tc(tc('#374151'))} />
                  <PolarAngleAxis dataKey="metric" tick={{ fill: tc('#9ca3af'), fontSize: 12 }} />
                  <PolarRadiusAxis tick={false} axisLine={false} domain={[0, 100]} />
                  <Radar dataKey="value" stroke={tc(tc('#3b82f6'))} fill={tc(tc('#3b82f6'))} fillOpacity={0.25} strokeWidth={2} />
                </RadarChart>
              </ResponsiveContainer>
            </div>
          )}
        </div>
      </div>

      {/* ===== KPI CARDS ===== */}
      <ProofGrid className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
        <KPICard label="Total Trips" value={formatNumber(s.total_trips)} icon={MapPin} color="blue"
          proof={{ dataset: 'driver.trips', params: { id: driverId }, value: num(s.total_trips) }} />
        <KPICard label="ETA Rate" value={formatPercent(s.eta_success_rate)} icon={Target} color={rateKpiColor(s.eta_success_rate)}
          proof={{ dataset: 'driver.eta', params: { id: driverId }, value: num(s.eta_success_rate) }} />
        <KPICard label="Avg Speed" value={formatSpeed(s.avg_speed_kmph)} icon={Gauge} color="cyan"
          proof={{ dataset: 'driver.speed', params: { id: driverId }, value: num(s.avg_speed_kmph) }} />
        <KPICard label="Avg Delay" value={s.avg_eta_delay_min != null ? `${Number(s.avg_eta_delay_min).toFixed(0)} min` : '—'} icon={Clock} color={s.avg_eta_delay_min <= 30 ? 'green' : 'amber'}
          proof={{ dataset: 'driver.delay', params: { id: driverId }, value: num(s.avg_eta_delay_min) }} />
      </ProofGrid>

      {/* ===== PERFORMANCE BAR + ROUTES/VEHICLES ===== */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-6">
        {/* Performance Bars */}
        <div className="bg-gray-900 rounded-xl border border-gray-800 p-5">
          <h2 className="text-lg font-semibold text-white mb-4 flex items-center gap-2">
            <Award className="w-5 h-5 text-amber-400" /> Performance Overview
          </h2>
          <div className="space-y-4">
            <div>
              <div className="flex justify-between text-sm mb-1">
                <span className="text-gray-400">ETA Compliance</span>
                <span className="text-emerald-400 font-semibold">{formatPercent(s.eta_success_rate)}</span>
              </div>
              <div className="w-full bg-gray-800 rounded-full h-3">
                <div className="bg-gradient-to-r from-emerald-600 to-emerald-400 h-3 rounded-full transition-all duration-700"
                  style={{ width: `${Math.min(s.eta_success_rate ?? 0, 100)}%` }} />
              </div>
            </div>
            <div>
              <div className="flex justify-between text-sm mb-1">
                <span className="text-gray-400">Avg Speed</span>
                <span className="text-blue-400 font-semibold">{formatSpeed(s.avg_speed_kmph)}</span>
              </div>
              <div className="w-full bg-gray-800 rounded-full h-3">
                <div className="bg-gradient-to-r from-blue-600 to-blue-400 h-3 rounded-full transition-all duration-700"
                  style={{ width: `${Math.min((s.avg_speed_kmph / 80) * 100, 100)}%` }} />
              </div>
            </div>
            <div>
              <div className="flex justify-between text-sm mb-1">
                <span className="text-gray-400">Avg Distance/Trip</span>
                <span className="text-purple-400 font-semibold">{formatDistance(s.avg_distance_km)}</span>
              </div>
              <div className="w-full bg-gray-800 rounded-full h-3">
                <div className="bg-gradient-to-r from-purple-600 to-purple-400 h-3 rounded-full transition-all duration-700"
                  style={{ width: `${Math.min((s.avg_distance_km / 1000) * 100, 100)}%` }} />
              </div>
            </div>
            <div>
              <div className="flex justify-between text-sm mb-1">
                <span className="text-gray-400">Vehicles Operated</span>
                <span className="text-cyan-400 font-semibold">{s.vehicles_used}</span>
              </div>
              <div className="w-full bg-gray-800 rounded-full h-3">
                <div className="bg-gradient-to-r from-cyan-600 to-cyan-400 h-3 rounded-full transition-all duration-700"
                  style={{ width: `${Math.min(s.vehicles_used * 10, 100)}%` }} />
              </div>
            </div>
          </div>
        </div>

        {/* Routes + Vehicles */}
        <div className="space-y-4">
          <div className="bg-gray-900 rounded-xl border border-gray-800 p-5">
            <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wider mb-3">Top Routes</h2>
            <div className="space-y-2 max-h-36 overflow-y-auto">
              {data.frequent_routes.slice(0, 6).map((r, i) => (
                <div key={i} className="flex justify-between text-sm items-center">
                  <span className="text-gray-300 truncate flex-1">{r.origin} → {r.destination}</span>
                  <span className="text-gray-500 text-xs ml-2 shrink-0">{r.trip_count} trips</span>
                </div>
              ))}
              {data.frequent_routes.length === 0 && <p className="text-gray-600 text-sm">No routes</p>}
            </div>
          </div>
          <div className="bg-gray-900 rounded-xl border border-gray-800 p-5">
            <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wider mb-3">Vehicles Operated</h2>
            <div className="space-y-2 max-h-36 overflow-y-auto">
              {data.vehicles_used.map(v => (
                <div key={v.id} className="flex justify-between text-sm">
                  <span className="text-gray-300">{v.asset_id} <span className="text-gray-600">({v.asset_type})</span></span>
                  <span className="text-gray-500 text-xs">{v.trip_count} trips</span>
                </div>
              ))}
              {data.vehicles_used.length === 0 && <p className="text-gray-600 text-sm">No vehicles</p>}
            </div>
          </div>
        </div>
      </div>

      {/* ===== TREND WITH FILTERS ===== */}
      <div className="bg-gray-900 rounded-xl border border-gray-800 p-5 mb-6">
        <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3 mb-4">
          <h2 className="text-lg font-semibold text-white flex items-center gap-2">
            <TrendingUp className="w-5 h-5 text-blue-400" /> Performance Trend
          </h2>
          <div className="flex flex-wrap items-center gap-2">
            <input type="date" value={dateFrom} onChange={e => setDateFrom(e.target.value)}
              className="bg-gray-800 border border-gray-700 rounded-lg px-2.5 py-1.5 text-sm text-gray-300 focus:ring-blue-500 focus:border-blue-500" />
            <span className="text-gray-500 text-xs">to</span>
            <input type="date" value={dateTo} onChange={e => setDateTo(e.target.value)}
              className="bg-gray-800 border border-gray-700 rounded-lg px-2.5 py-1.5 text-sm text-gray-300 focus:ring-blue-500 focus:border-blue-500" />
            <select value={groupBy} onChange={e => setGroupBy(e.target.value as any)}
              className="bg-gray-800 border border-gray-700 rounded-lg px-2.5 py-1.5 text-sm text-gray-300 focus:ring-blue-500 focus:border-blue-500">
              <option value="month">Monthly</option>
              <option value="week">Weekly</option>
              <option value="day">Daily</option>
            </select>
            {(dateFrom || dateTo) && (
              <button onClick={() => { setDateFrom(''); setDateTo(''); }} className="text-xs text-gray-500 hover:text-white">Clear</button>
            )}
          </div>
        </div>

        {trendLoading ? <Spinner /> : trend && trend.length > 0 ? (
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            {/* Trip Count + ETA Rate (Composed) */}
            <div>
              <h3 className="text-sm text-gray-400 mb-2">Trips & ETA Rate</h3>
              <ResponsiveContainer width="100%" height={260}>
                <ComposedChart data={trend}>
                  <CartesianGrid strokeDasharray="3 3" stroke={CHART_COLORS.grid} />
                  <XAxis dataKey="period" tick={{ fill: tc('#9ca3af'), fontSize: 12 }} axisLine={false} tickLine={false} />
                  <YAxis yAxisId="left" tick={{ fill: tc('#9ca3af'), fontSize: 12 }} axisLine={false} tickLine={false} />
                  <YAxis yAxisId="right" orientation="right" tick={{ fill: tc('#9ca3af'), fontSize: 12 }} axisLine={false} tickLine={false} domain={[0, 100]} />
                  <Tooltip contentStyle={TOOLTIP_STYLE} />
                  <Bar yAxisId="left" dataKey="trip_count" name="Trips" fill={tc(tc('#3b82f6'))} radius={[4, 4, 0, 0]} />
                  <Line yAxisId="right" type="monotone" dataKey="eta_success_rate" name="ETA Rate %" stroke={tc(tc('#10b981'))} strokeWidth={2} dot={{ r: 3, fill: tc('#10b981') }} />
                </ComposedChart>
              </ResponsiveContainer>
            </div>
            {/* Speed + Delay */}
            <div>
              <h3 className="text-sm text-gray-400 mb-2">Speed & Avg Delay</h3>
              <ResponsiveContainer width="100%" height={260}>
                <RechartsArea data={trend}>
                  <CartesianGrid strokeDasharray="3 3" stroke={CHART_COLORS.grid} />
                  <XAxis dataKey="period" tick={{ fill: tc('#9ca3af'), fontSize: 12 }} axisLine={false} tickLine={false} />
                  <YAxis tick={{ fill: tc('#9ca3af'), fontSize: 12 }} axisLine={false} tickLine={false} />
                  <Tooltip contentStyle={TOOLTIP_STYLE} />
                  <Area type="monotone" dataKey="avg_speed" name="Avg Speed (km/h)" stroke={tc(tc('#06b6d4'))} fill={tc(tc('#06b6d4'))} fillOpacity={0.15} strokeWidth={2} />
                  <Area type="monotone" dataKey="avg_delay" name="Avg Delay (min)" stroke={tc(tc('#f59e0b'))} fill={tc(tc('#f59e0b'))} fillOpacity={0.1} strokeWidth={2} />
                </RechartsArea>
              </ResponsiveContainer>
            </div>
          </div>
        ) : (
          <p className="text-gray-600 text-sm py-8 text-center">No trend data for selected period</p>
        )}
      </div>

      {/* ===== INSIGHTS: window selector (drives pattern + alerts) ===== */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 mb-4">
        <h2 className="text-lg font-semibold text-white flex items-center gap-2">
          <Zap className="w-5 h-5 text-yellow-400" /> Journey Insights
          {drivingPattern?.trip_count != null && (
            <span className="text-xs text-gray-500 font-normal">· {drivingPattern.trip_count} trip{drivingPattern.trip_count !== 1 ? 's' : ''} in window</span>
          )}
        </h2>
        <div className="flex flex-wrap gap-1.5">
          {WINDOWS.map(w => (
            <button key={w.key} onClick={() => setInsightWindow(w.key)}
              className={`px-2.5 py-1.5 rounded-lg text-xs font-medium border transition-colors ${
                insightWindow === w.key
                  ? 'bg-blue-600 border-blue-500 text-white'
                  : 'bg-gray-800 border-gray-700 text-gray-400 hover:text-gray-200'}`}>
              {w.label}
            </button>
          ))}
        </div>
      </div>

      {insightsLoading ? <div className="mb-6"><Spinner /></div> : (
        <>
          {/* ===== SAFETY ALERTS (derived from GPS) ===== */}
          {alerts && (
            <div className="bg-gray-900 rounded-xl border border-gray-800 p-5 mb-6">
              <div className="flex items-center justify-between flex-wrap gap-3 mb-4">
                <h3 className="text-base font-semibold text-white flex items-center gap-2">
                  <ShieldAlert className="w-5 h-5 text-red-400" /> Safety Alerts
                  <span className="text-xs text-gray-500 font-normal">{alerts.summary.total} event{alerts.summary.total !== 1 ? 's' : ''} from GPS</span>
                </h3>
                <div className={`flex items-center gap-2 px-3 py-1.5 rounded-lg border ${
                  alerts.summary.safety_score >= 80 ? 'bg-emerald-950/40 border-emerald-800/50 text-emerald-400'
                  : alerts.summary.safety_score >= 60 ? 'bg-amber-950/40 border-amber-800/50 text-amber-400'
                  : 'bg-red-950/40 border-red-800/50 text-red-400'}`}>
                  <ShieldCheck className="w-4 h-4" />
                  <span className="text-sm font-semibold">Safety score {alerts.summary.safety_score}</span>
                </div>
              </div>

              {alerts.summary.total === 0 ? (
                <p className="text-gray-500 text-sm py-6 text-center flex items-center justify-center gap-2">
                  <ShieldCheck className="w-4 h-4 text-emerald-500" /> No safety events in this window — clean driving.
                </p>
              ) : (
                <>
                  {/* Alert-type KPI chips */}
                  <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-5">
                    {['overspeed', 'harsh_driving', 'long_stop', 'night_driving'].map(tp => {
                      const meta = TYPE_META[tp];
                      const Icon = meta.icon;
                      return (
                        <div key={tp} className="bg-gray-800/50 rounded-lg p-3 flex items-center gap-3">
                          <Icon className={`w-5 h-5 ${meta.color}`} />
                          <div>
                            <p className="text-lg font-bold text-white leading-none">{alerts.summary.by_type[tp] ?? 0}</p>
                            <p className="text-xs text-gray-500 mt-0.5">{meta.label}</p>
                          </div>
                        </div>
                      );
                    })}
                  </div>

                  <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
                    {/* Severity donut */}
                    <div>
                      <h4 className="text-sm text-gray-400 mb-2">By severity</h4>
                      <ResponsiveContainer width="100%" height={200}>
                        <PieChart>
                          <Pie data={Object.entries(alerts.summary.by_severity).map(([k, v]) => ({ name: SEV_META[k]?.label ?? k, value: v, sev: k }))}
                            dataKey="value" nameKey="name" cx="50%" cy="50%" innerRadius={45} outerRadius={75} paddingAngle={2}>
                            {Object.entries(alerts.summary.by_severity).map(([k], i) => (
                              <Cell key={i} fill={SEV_META[k]?.color ?? tc('#6b7280')} />
                            ))}
                          </Pie>
                          <Tooltip contentStyle={TOOLTIP_STYLE} />
                        </PieChart>
                      </ResponsiveContainer>
                    </div>

                    {/* Recent alerts list */}
                    <div className="lg:col-span-2">
                      <h4 className="text-sm text-gray-400 mb-2">Recent events</h4>
                      <div className="space-y-2 max-h-[220px] overflow-y-auto pr-1">
                        {alerts.alerts.map((a, i) => {
                          const sm = SEV_META[a.severity] ?? SEV_META.info;
                          const Icon = sm.icon;
                          return (
                            <div key={i} className={`flex items-start gap-3 rounded-lg border p-2.5 ${sm.bg}`}>
                              <Icon className="w-4 h-4 mt-0.5 shrink-0" style={{ color: sm.color }} />
                              <div className="min-w-0 flex-1">
                                <p className="text-sm text-gray-200 font-medium">{a.title}</p>
                                <p className="text-xs text-gray-500 truncate">{a.message}</p>
                              </div>
                              <span className="text-xs text-gray-500 shrink-0 whitespace-nowrap">{formatDateTime(a.at)}</span>
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  </div>
                </>
              )}
            </div>
          )}

          {/* ===== DRIVING PATTERN ===== */}
          {drivingPattern && drivingPattern.stats ? (
            <div className="bg-gray-900 rounded-xl border border-gray-800 p-5 mb-6">
              <h3 className="text-base font-semibold text-white mb-4 flex items-center gap-2">
                <Activity className="w-5 h-5 text-cyan-400" /> Driving Pattern
                <span className="text-xs text-gray-500 font-normal">
                  {formatNumber(drivingPattern.stats.total_points)} GPS points · {drivingPattern.stats.total_days} days
                </span>
              </h3>

              {/* Quick stats */}
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-5">
                <div className="bg-gray-800/50 rounded-lg p-3 text-center">
                  <p className="text-xs text-gray-500">Avg Moving Speed</p>
                  <p className="text-lg font-bold text-cyan-400">{formatSpeed(drivingPattern.stats.overall_avg_speed)}</p>
                </div>
                <div className="bg-gray-800/50 rounded-lg p-3 text-center">
                  <p className="text-xs text-gray-500">Top Speed</p>
                  <p className="text-lg font-bold text-red-400">{formatSpeed(drivingPattern.stats.top_speed)}</p>
                </div>
                <div className="bg-gray-800/50 rounded-lg p-3 text-center">
                  <p className="text-xs text-gray-500">Distance Tracked</p>
                  <p className="text-lg font-bold text-purple-400">{formatDistance(drivingPattern.stats.total_distance_tracked)}</p>
                </div>
                <div className="bg-gray-800/50 rounded-lg p-3 text-center">
                  <p className="text-xs text-gray-500">Moving Time</p>
                  <p className="text-lg font-bold text-emerald-400">{formatPercent(drivingPattern.stats.moving_pct)}</p>
                </div>
              </div>

              <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
                {/* Hour-of-day travel share — the key "when does he drive" view */}
                <div className="lg:col-span-2">
                  <h4 className="text-sm text-gray-400 mb-2">When does he travel? — share of distance by hour of day</h4>
                  <ResponsiveContainer width="100%" height={260}>
                    <ComposedChart data={drivingPattern.hourly_pattern}>
                      <CartesianGrid strokeDasharray="3 3" stroke={CHART_COLORS.grid} />
                      <XAxis dataKey="hour_of_day" tick={{ fill: tc('#9ca3af'), fontSize: 12 }} axisLine={false} tickLine={false}
                        tickFormatter={(h: number) => `${String(h).padStart(2, '0')}h`} />
                      <YAxis yAxisId="left" tick={{ fill: tc('#9ca3af'), fontSize: 12 }} axisLine={false} tickLine={false}
                        tickFormatter={(v: number) => `${v}%`} />
                      <YAxis yAxisId="right" orientation="right" tick={{ fill: tc('#9ca3af'), fontSize: 12 }} axisLine={false} tickLine={false} />
                      <Tooltip contentStyle={TOOLTIP_STYLE}
                        labelFormatter={(h: any) => `${String(h).padStart(2, '0')}:00 – ${String((Number(h) + 1) % 24).padStart(2, '0')}:00`}
                        formatter={(val: any, name: any) => name === '% of distance' ? [`${val}%`, name] : [`${val} km/h`, name]} />
                      <Bar yAxisId="left" dataKey="distance_share_pct" name="% of distance" fill={tc(tc('#3b82f6'))} radius={[4, 4, 0, 0]} />
                      <Line yAxisId="right" type="monotone" dataKey="avg_speed" name="Avg speed" stroke={tc(tc('#f59e0b'))} strokeWidth={2} dot={false} />
                    </ComposedChart>
                  </ResponsiveContainer>
                </div>

                {/* Speed Distribution */}
                {drivingPattern.speed_distribution.length > 0 && (
                  <div>
                    <h4 className="text-sm text-gray-400 mb-2">Speed Distribution</h4>
                    <ResponsiveContainer width="100%" height={240}>
                      <RechartsBar data={drivingPattern.speed_distribution} layout="vertical">
                        <CartesianGrid strokeDasharray="3 3" stroke={CHART_COLORS.grid} />
                        <XAxis type="number" tick={{ fill: tc('#9ca3af'), fontSize: 12 }} axisLine={false} tickLine={false} />
                        <YAxis dataKey="speed_range" type="category" tick={{ fill: tc('#9ca3af'), fontSize: 12 }} axisLine={false} tickLine={false} width={100} />
                        <Tooltip contentStyle={TOOLTIP_STYLE} />
                        <Bar dataKey="count" name="GPS points" radius={[0, 4, 4, 0]}>
                          {drivingPattern.speed_distribution.map((_, i) => (
                            <Cell key={i} fill={PIE_COLORS[i % PIE_COLORS.length]} />
                          ))}
                        </Bar>
                      </RechartsBar>
                    </ResponsiveContainer>
                  </div>
                )}

                {/* Day-of-week distance */}
                {drivingPattern.daily_pattern.length > 0 && (
                  <div>
                    <h4 className="text-sm text-gray-400 mb-2">Day-of-Week Activity</h4>
                    <ResponsiveContainer width="100%" height={240}>
                      <ComposedChart data={drivingPattern.daily_pattern}>
                        <CartesianGrid strokeDasharray="3 3" stroke={CHART_COLORS.grid} />
                        <XAxis dataKey="day_name" tick={{ fill: tc('#9ca3af'), fontSize: 12 }} axisLine={false} tickLine={false}
                          tickFormatter={(d: string) => d?.slice(0, 3)} />
                        <YAxis yAxisId="left" tick={{ fill: tc('#9ca3af'), fontSize: 12 }} axisLine={false} tickLine={false} />
                        <YAxis yAxisId="right" orientation="right" tick={{ fill: tc('#9ca3af'), fontSize: 12 }} axisLine={false} tickLine={false} />
                        <Tooltip contentStyle={TOOLTIP_STYLE} />
                        <Bar yAxisId="left" dataKey="dist_km" name="Distance (km)" fill="#8b5cf6" radius={[4, 4, 0, 0]} />
                        <Line yAxisId="right" type="monotone" dataKey="avg_speed" name="Avg speed (km/h)" stroke={tc(tc('#06b6d4'))} strokeWidth={2} dot={false} />
                      </ComposedChart>
                    </ResponsiveContainer>
                  </div>
                )}
              </div>
            </div>
          ) : (
            <div className="bg-gray-900 rounded-xl border border-gray-800 p-5 mb-6">
              <p className="text-gray-500 text-sm py-6 text-center">No GPS-based driving pattern for this window.</p>
            </div>
          )}
        </>
      )}

      {/* ===== RECENT TRIPS TABLE ===== */}
      <div className="bg-gray-900 rounded-xl border border-gray-800 p-5">
        <h2 className="text-lg font-semibold text-white mb-4">Recent Trips</h2>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-gray-800">
                <th className="px-3 py-2 text-left text-xs text-gray-400">Dispatch#</th>
                <th className="px-3 py-2 text-left text-xs text-gray-400">Origin</th>
                <th className="px-3 py-2 text-left text-xs text-gray-400">Destination</th>
                <th className="px-3 py-2 text-left text-xs text-gray-400">Start</th>
                <th className="px-3 py-2 text-right text-xs text-gray-400">Duration</th>
                <th className="px-3 py-2 text-center text-xs text-gray-400">ETA Met</th>
                <th className="px-3 py-2 text-right text-xs text-gray-400">Speed</th>
                <th className="px-3 py-2 text-right text-xs text-gray-400">Distance</th>
              </tr>
            </thead>
            <tbody>
              {data.recent_trips.map(t => (
                <tr key={t.id} onClick={() => navigate(`/trips/${t.id}`)}
                  className="border-b border-gray-800/50 hover:bg-gray-800/50 cursor-pointer transition-colors">
                  <td className="px-3 py-2 text-gray-300">{t.dispatch_entry_no}</td>
                  <td className="px-3 py-2 text-gray-300">{t.origin_name}</td>
                  <td className="px-3 py-2 text-gray-300">{t.destination_name}</td>
                  <td className="px-3 py-2 text-gray-400">{formatDateTime(t.trip_start)}</td>
                  <td className="px-3 py-2 text-right text-gray-300">{formatDuration(t.trip_duration_minutes)}</td>
                  <td className="px-3 py-2 text-center">
                    <Badge label={t.eta_met ? 'Yes' : 'No'} variant={t.eta_met ? 'success' : 'danger'} />
                  </td>
                  <td className="px-3 py-2 text-right text-gray-300">{formatSpeed(t.avg_speed_kmph)}</td>
                  <td className="px-3 py-2 text-right text-gray-300">{formatDistance(t.trip_km)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </PageContainer>
  );
}
