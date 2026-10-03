import { useState } from 'react';
import {
  Play, Trash2, Clock, MapPin, Users, TrendingUp, TrendingDown,
  Star, Award, AlertTriangle, CheckCircle, Calendar, Truck,
  ShieldCheck, Brain, Building2, Zap, Search,
} from 'lucide-react';
import {
  ResponsiveContainer, BarChart as RechartsBar, Bar, XAxis, YAxis,
  CartesianGrid, Tooltip, Cell,
} from 'recharts';
import PageContainer from '../components/layout/PageContainer';
import ModelCard from '../components/ml/ModelCard';
import PredictionForm from '../components/ml/PredictionForm';
import Spinner from '../components/ui/Spinner';
import { useApi } from '../hooks/useApi';
import {
  listModels, predictEta, predictSla, scanAnomalies, recommendDrivers,
  forecastTrips, getFleetFatigue, getClientForecast,
  trainAllModels, trainTier, clearModelCache,
} from '../services/ml';
import type { MLModel } from '../types/ml';
import { tc } from '../../../core/theme';

// ──────────────────────────────────────────────
// Tab definitions
// ──────────────────────────────────────────────
const TABS = [
  { key: 'eta', label: 'ETA Prediction', icon: Clock },
  { key: 'sla', label: 'SLA Prediction', icon: ShieldCheck },
  { key: 'anomaly', label: 'Anomaly Scanner', icon: AlertTriangle },
  { key: 'recommend', label: 'Driver Recommender', icon: Users },
  { key: 'fatigue', label: 'Fatigue Monitor', icon: Brain },
  { key: 'forecast', label: 'Trip Forecast', icon: TrendingUp },
  { key: 'client', label: 'Client Forecast', icon: Building2 },
] as const;

type TabKey = typeof TABS[number]['key'];

function getDefaultTripStart(): string {
  const now = new Date();
  now.setMinutes(now.getMinutes() - now.getTimezoneOffset());
  return now.toISOString().slice(0, 16);
}

const ETA_FIELDS = [
  { name: 'origin', label: 'Origin', type: 'text' as const, required: true, placeholder: 'e.g. Mumbai' },
  { name: 'destination', label: 'Destination', type: 'text' as const, required: true, placeholder: 'e.g. Delhi' },
  { name: 'trip_start', label: 'Trip Start Date & Time', type: 'datetime-local' as const, required: true, defaultValue: getDefaultTripStart() },
  { name: 'driver_id', label: 'Driver ID', type: 'number' as const },
  { name: 'vehicle_id', label: 'Vehicle ID', type: 'number' as const },
  { name: 'trip_km', label: 'Trip Distance (km)', type: 'number' as const },
];

const SLA_FIELDS = [
  { name: 'origin', label: 'Origin', type: 'text' as const, required: true, placeholder: 'e.g. Mumbai' },
  { name: 'destination', label: 'Destination', type: 'text' as const, required: true, placeholder: 'e.g. Delhi' },
  { name: 'trip_start', label: 'Trip Start Date & Time', type: 'datetime-local' as const, required: true, defaultValue: getDefaultTripStart() },
  { name: 'driver_id', label: 'Driver ID', type: 'number' as const },
  { name: 'vehicle_id', label: 'Vehicle ID', type: 'number' as const },
  { name: 'trip_km', label: 'Trip Distance (km)', type: 'number' as const },
];

const RECOMMEND_FIELDS = [
  { name: 'origin', label: 'Origin', type: 'text' as const, required: true, placeholder: 'e.g. Mumbai' },
  { name: 'destination', label: 'Destination', type: 'text' as const, required: true, placeholder: 'e.g. Delhi' },
  { name: 'top_n', label: 'Top N Drivers', type: 'number' as const, placeholder: '10' },
];

// ──────────────────────────────────────────────
// Helpers
// ──────────────────────────────────────────────
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

function ErrorCard({ message }: { message: string }) {
  return (
    <div className="bg-red-950/30 border border-red-800/40 rounded-lg p-4 flex items-center gap-3">
      <AlertTriangle className="w-5 h-5 text-red-400 shrink-0" />
      <p className="text-sm text-red-300">{message}</p>
    </div>
  );
}

function StatCard({ label, value, icon }: { label: string; value: string | number; icon: React.ReactNode }) {
  return (
    <div className="bg-gray-900 rounded-lg border border-gray-800 p-3">
      <div className="flex items-center gap-1.5 mb-1">
        {icon}
        <p className="text-xs text-gray-500">{label}</p>
      </div>
      <p className="text-lg font-bold text-white">{value}</p>
    </div>
  );
}

const BAR_COLORS = [tc('#3b82f6'), '#6366f1', '#8b5cf6', tc('#a855f7'), tc('#2563eb'), '#4f46e5', '#7c3aed'];

// ──────────────────────────────────────────────
// ETA Result
// ──────────────────────────────────────────────
function ETAResult({ result, tripStart }: { result: any; tripStart?: string }) {
  if (result?.error) return <ErrorCard message={result.error} />;
  const predictedMinutes = result?.predicted_duration_minutes;
  if (predictedMinutes == null) return <ErrorCard message="No prediction returned" />;

  const startDate = tripStart ? new Date(tripStart) : new Date();
  const arrivalDate = new Date(startDate.getTime() + predictedMinutes * 60 * 1000);
  const durationStr = fmtDuration(predictedMinutes);

  const fmtDate = (d: Date) => d.toLocaleDateString('en-IN', { weekday: 'long', year: 'numeric', month: 'long', day: 'numeric' });
  const fmtTime = (d: Date) => d.toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', hour12: true });

  return (
    <div className="space-y-4">
      <div className="bg-gradient-to-br from-blue-900/60 to-indigo-900/60 rounded-xl border border-blue-700/40 p-5">
        <div className="flex items-center gap-2 mb-3">
          <Calendar className="w-5 h-5 text-blue-400" />
          <span className="text-sm font-medium text-blue-300">Estimated Arrival</span>
        </div>
        <p className="text-2xl font-bold text-white mb-1">{fmtDate(arrivalDate)}</p>
        <p className="text-3xl font-bold text-blue-400">{fmtTime(arrivalDate)}</p>
      </div>
      <div className="grid grid-cols-2 gap-3">
        <div className="bg-gray-900 rounded-lg border border-gray-800 p-3">
          <p className="text-xs text-gray-500 mb-1">Trip Duration</p>
          <p className="text-lg font-bold text-white">{durationStr}</p>
          <p className="text-xs text-gray-500">{predictedMinutes.toFixed(0)} minutes</p>
        </div>
        <div className="bg-gray-900 rounded-lg border border-gray-800 p-3">
          <p className="text-xs text-gray-500 mb-1">Departure</p>
          <p className="text-sm font-semibold text-white">{fmtDate(startDate)}</p>
          <p className="text-sm text-gray-400">{fmtTime(startDate)}</p>
        </div>
      </div>
      {(result.route_avg_duration != null || result.driver_avg_duration != null) && (
        <div className="bg-gray-900 rounded-lg border border-gray-800 p-3">
          <p className="text-xs text-gray-500 mb-2">Comparison</p>
          <div className="space-y-2">
            {result.route_avg_duration != null && (
              <div className="flex justify-between items-center">
                <span className="text-xs text-gray-400">Route Average</span>
                <span className="text-sm text-gray-300">{fmtDuration(result.route_avg_duration)}</span>
              </div>
            )}
            {result.driver_avg_duration != null && (
              <div className="flex justify-between items-center">
                <span className="text-xs text-gray-400">Driver Average</span>
                <span className="text-sm text-gray-300">{fmtDuration(result.driver_avg_duration)}</span>
              </div>
            )}
            <div className="flex justify-between items-center border-t border-gray-800 pt-2">
              <span className="text-xs text-gray-400">ML Predicted</span>
              <span className="text-sm font-semibold text-blue-400">{fmtDuration(predictedMinutes)}</span>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// ──────────────────────────────────────────────
// SLA Result
// ──────────────────────────────────────────────
function SLAResult({ result }: { result: any }) {
  if (result?.error) return <ErrorCard message={result.error} />;

  const prob = result?.on_time_probability;
  const riskLevel = result?.risk_level || 'unknown';
  const prediction = result?.prediction;
  const factors = result?.contributing_factors || {};

  const riskStyles: Record<string, { bg: string; text: string; border: string }> = {
    low: { bg: 'from-emerald-900/50 to-green-900/40', text: 'text-emerald-400', border: 'border-emerald-700/40' },
    medium: { bg: 'from-amber-900/50 to-yellow-900/40', text: 'text-amber-400', border: 'border-amber-700/40' },
    high: { bg: 'from-orange-900/50 to-red-900/40', text: 'text-orange-400', border: 'border-orange-700/40' },
    critical: { bg: 'from-red-900/50 to-red-950/60', text: 'text-red-400', border: 'border-red-700/40' },
  };
  const style = riskStyles[riskLevel] || riskStyles.medium;

  return (
    <div className="space-y-4">
      <div className={`bg-gradient-to-br ${style.bg} rounded-xl border ${style.border} p-5`}>
        <div className="flex items-center gap-3 mb-2">
          <ShieldCheck className={`w-8 h-8 ${style.text}`} />
          <div>
            <p className="text-xl font-bold text-white capitalize">{riskLevel} Risk</p>
            <p className="text-sm text-gray-400">{prediction === 'on_time' ? 'Likely on time' : 'Likely delayed'}</p>
          </div>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-3">
        <div className="bg-gray-900 rounded-lg border border-gray-800 p-3">
          <p className="text-xs text-gray-500 mb-1">On-Time Probability</p>
          <p className={`text-2xl font-bold ${prob >= 0.7 ? 'text-emerald-400' : prob >= 0.4 ? 'text-amber-400' : 'text-red-400'}`}>
            {(prob * 100).toFixed(1)}%
          </p>
        </div>
        <div className="bg-gray-900 rounded-lg border border-gray-800 p-3">
          <p className="text-xs text-gray-500 mb-1">Risk Level</p>
          <p className={`text-2xl font-bold capitalize ${style.text}`}>{riskLevel}</p>
        </div>
      </div>

      {Object.keys(factors).length > 0 && (
        <div className="bg-gray-900 rounded-lg border border-gray-800 p-3">
          <p className="text-xs text-gray-500 mb-2">Contributing Factors</p>
          <div className="space-y-2">
            {Object.entries(factors)
              .sort(([, a], [, b]) => Math.abs(b as number) - Math.abs(a as number))
              .slice(0, 8)
              .map(([k, v]) => {
                const val = v as number;
                const isPositive = val > 0;
                return (
                  <div key={k} className="flex items-center gap-2">
                    <span className="text-xs text-gray-400 flex-1 truncate">{k.replace(/_/g, ' ')}</span>
                    <div className="w-24 bg-gray-800 rounded-full h-1.5">
                      <div
                        className={`h-1.5 rounded-full ${isPositive ? 'bg-emerald-500' : 'bg-red-500'}`}
                        style={{ width: `${Math.min(Math.abs(val) * 100, 100)}%` }}
                      />
                    </div>
                    <span className={`text-xs font-mono w-12 text-right ${isPositive ? 'text-emerald-400' : 'text-red-400'}`}>
                      {isPositive ? '+' : ''}{(val * 100).toFixed(0)}%
                    </span>
                  </div>
                );
              })}
          </div>
        </div>
      )}
    </div>
  );
}

// ──────────────────────────────────────────────
// Anomaly Scanner Result
// ──────────────────────────────────────────────
function AnomalyScanPanel() {
  const [days, setDays] = useState(7);
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<any>(null);

  const handleScan = async () => {
    setLoading(true);
    setResult(null);
    try {
      const res = await scanAnomalies(days);
      setResult(res.data);
    } catch (err: any) {
      setResult({ error: err?.response?.data?.detail || err.message || 'Scan failed' });
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-4">
        <div>
          <label className="block text-xs text-gray-400 mb-1">Scan Last N Days</label>
          <input
            type="number" value={days} min={1} max={90}
            onChange={e => setDays(Number(e.target.value))}
            className="w-24 px-3 py-2 bg-gray-800 border border-gray-700 rounded-lg text-sm text-gray-200 focus:outline-none focus:border-blue-500"
          />
        </div>
        <button onClick={handleScan} disabled={loading}
          className="flex items-center gap-2 px-6 py-2.5 bg-red-600 hover:bg-red-700 text-white rounded-lg text-sm font-medium disabled:opacity-50 transition-colors mt-5">
          {loading ? <div className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" /> : <Zap className="w-4 h-4" />}
          {loading ? 'Scanning...' : 'Scan for Anomalies'}
        </button>
      </div>

      {loading && !result && <Spinner />}

      {result && (
        result.error ? <ErrorCard message={result.error} /> : (
          <div className="space-y-4">
            <div className={`rounded-xl border p-5 ${
              result.anomalies_found > 0
                ? 'bg-gradient-to-br from-red-900/50 to-orange-900/40 border-red-700/40'
                : 'bg-gradient-to-br from-emerald-900/50 to-green-900/40 border-emerald-700/40'
            }`}>
              <div className="flex items-center gap-3">
                {result.anomalies_found > 0
                  ? <AlertTriangle className="w-8 h-8 text-red-400" />
                  : <CheckCircle className="w-8 h-8 text-emerald-400" />}
                <div>
                  <p className="text-xl font-bold text-white">
                    {result.anomalies_found > 0 ? `${result.anomalies_found} Anomalies Found` : 'No Anomalies'}
                  </p>
                  <p className="text-sm text-gray-400">Scanned {result.scanned_trips} trips from last {result.scan_days || days} days</p>
                </div>
              </div>
            </div>

            <div className="grid grid-cols-3 gap-3">
              <StatCard label="Trips Scanned" value={result.scanned_trips ?? 0} icon={<Truck className="w-4 h-4 text-blue-400" />} />
              <StatCard label="Anomalies" value={result.anomalies_found ?? 0} icon={<AlertTriangle className="w-4 h-4 text-red-400" />} />
              <StatCard label="Alerts Created" value={result.alerts_created ?? 0} icon={<Zap className="w-4 h-4 text-amber-400" />} />
            </div>

            {result.severity_breakdown && Object.keys(result.severity_breakdown).length > 0 && (
              <div className="bg-gray-900 rounded-lg border border-gray-800 p-3">
                <p className="text-xs text-gray-500 mb-2">Severity Breakdown</p>
                <div className="flex gap-4">
                  {Object.entries(result.severity_breakdown).map(([sev, count]) => {
                    const sevColors: Record<string, string> = {
                      high: 'text-red-400', medium: 'text-amber-400', low: 'text-emerald-400',
                    };
                    return (
                      <div key={sev} className="flex items-center gap-2">
                        <span className={`text-sm font-bold ${sevColors[sev] || 'text-gray-400'}`}>{String(count)}</span>
                        <span className="text-xs text-gray-500 capitalize">{sev}</span>
                      </div>
                    );
                  })}
                </div>
              </div>
            )}
          </div>
        )
      )}
    </div>
  );
}

// ──────────────────────────────────────────────
// Fatigue Monitor
// ──────────────────────────────────────────────
function FatiguePanel() {
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<any>(null);

  const handleLoad = async () => {
    setLoading(true);
    setResult(null);
    try {
      const res = await getFleetFatigue();
      setResult(res.data);
    } catch (err: any) {
      setResult({ error: err?.response?.data?.detail || err.message || 'Failed to load fatigue data' });
    } finally {
      setLoading(false);
    }
  };

  const riskColors: Record<string, string> = {
    critical: 'text-red-400',
    high: 'text-orange-400',
    medium: 'text-amber-400',
    low: 'text-emerald-400',
  };

  const riskBg: Record<string, string> = {
    critical: 'bg-red-950/40 border-red-800/40',
    high: 'bg-orange-950/40 border-orange-800/40',
    medium: 'bg-amber-950/40 border-amber-800/40',
    low: 'bg-emerald-950/40 border-emerald-800/40',
  };

  return (
    <div className="space-y-4">
      <button onClick={handleLoad} disabled={loading}
        className="flex items-center gap-2 px-6 py-3 bg-purple-600 hover:bg-purple-700 text-white rounded-lg text-sm font-medium disabled:opacity-50 transition-colors">
        {loading ? <div className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" /> : <Brain className="w-4 h-4" />}
        {loading ? 'Loading...' : 'Check Fleet Fatigue'}
      </button>

      {loading && !result && <Spinner />}

      {result && (
        result.error ? <ErrorCard message={result.error} /> : (
          <div className="space-y-4">
            {/* Summary cards */}
            {result.summary && (
              <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                <StatCard label="Critical" value={result.summary.critical ?? 0} icon={<AlertTriangle className="w-4 h-4 text-red-400" />} />
                <StatCard label="High" value={result.summary.high ?? 0} icon={<AlertTriangle className="w-4 h-4 text-orange-400" />} />
                <StatCard label="Medium" value={result.summary.medium ?? 0} icon={<Brain className="w-4 h-4 text-amber-400" />} />
                <StatCard label="Low" value={result.summary.low ?? 0} icon={<CheckCircle className="w-4 h-4 text-emerald-400" />} />
              </div>
            )}

            {/* Driver cards */}
            {result.drivers && result.drivers.length > 0 && (
              <div className="space-y-2 max-h-[500px] overflow-y-auto pr-1">
                {result.drivers.map((d: any) => (
                  <div key={d.driver_id} className={`rounded-lg border p-3 ${riskBg[d.risk_level] || 'bg-gray-900 border-gray-800'}`}>
                    <div className="flex items-center gap-3">
                      <div className="min-w-0 flex-1">
                        <p className="text-sm font-semibold text-white truncate">{d.driver_name || `Driver #${d.driver_id}`}</p>
                        <p className="text-xs text-gray-500">ID: {d.driver_id}</p>
                      </div>
                      <div className="text-right shrink-0">
                        <p className={`text-lg font-bold ${riskColors[d.risk_level] || 'text-gray-400'}`}>
                          {typeof d.fatigue_score === 'number' ? d.fatigue_score.toFixed(1) : d.fatigue_score}
                        </p>
                        <p className="text-xs text-gray-500 uppercase">Fatigue Score</p>
                      </div>
                      <span className={`px-2 py-0.5 rounded text-xs font-medium capitalize ${riskColors[d.risk_level] || 'text-gray-400'}`}>
                        {d.risk_level}
                      </span>
                    </div>
                    {d.factors && (
                      <div className="mt-2 flex gap-4 text-xs text-gray-400 flex-wrap">
                        {d.hours_driving_24h != null && <span>24h: {d.hours_driving_24h}h</span>}
                        {d.hours_driving_7d != null && <span>7d: {d.hours_driving_7d}h</span>}
                        {d.consecutive_days != null && <span>{d.consecutive_days} consec. days</span>}
                        {d.night_trips_ratio != null && <span>Night: {(d.night_trips_ratio * 100).toFixed(0)}%</span>}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>
        )
      )}
    </div>
  );
}

// ──────────────────────────────────────────────
// Driver Recommender Result (v2 — two-tier)
// ──────────────────────────────────────────────
function RecommenderResult({ result }: { result: any }) {
  if (result?.error) return <ErrorCard message={result.error} />;

  const experienced = result?.experienced_on_route || [];
  const similar = result?.similar_route_experience || [];
  const allDrivers = result?.recommended_drivers || [];

  if (experienced.length === 0 && similar.length === 0 && allDrivers.length === 0)
    return <ErrorCard message="No drivers found for this route" />;

  const getRankIcon = (idx: number) => {
    if (idx === 0) return <Award className="w-5 h-5 text-yellow-400" />;
    if (idx === 1) return <Award className="w-5 h-5 text-gray-300" />;
    if (idx === 2) return <Award className="w-5 h-5 text-amber-600" />;
    return <span className="w-5 h-5 flex items-center justify-center text-xs font-bold text-gray-500">#{idx + 1}</span>;
  };

  const getScoreColor = (score: number) =>
    score >= 70 ? 'text-emerald-400' : score >= 50 ? 'text-amber-400' : 'text-red-400';
  const getScoreBg = (score: number) =>
    score >= 70 ? 'bg-emerald-950/50 border-emerald-800/40' : score >= 50 ? 'bg-amber-950/50 border-amber-800/40' : 'bg-red-950/50 border-red-800/40';

  const renderDriverCard = (d: any, idx: number, highlight: boolean) => (
    <div key={d.driver_id} className={`rounded-lg border p-3 transition-colors hover:border-gray-600 ${
      highlight && idx === 0 ? 'bg-gradient-to-r from-yellow-950/30 to-gray-900 border-yellow-800/30' : 'bg-gray-900 border-gray-800'
    }`}>
      <div className="flex items-center gap-3">
        <div className="shrink-0">{getRankIcon(idx)}</div>
        <div className="min-w-0 flex-1">
          <p className="text-sm font-semibold text-white truncate">{d.driver_name || `Driver #${d.driver_id}`}</p>
          <div className="flex items-center gap-2 text-xs text-gray-500">
            <span>ID: {d.driver_id}</span>
            {d.has_route_experience && (
              <span className="flex items-center gap-0.5 text-blue-400">
                <Star className="w-3 h-3" /> {d.route_trips} route trips
              </span>
            )}
            <span>{d.total_trips} total trips</span>
          </div>
        </div>
        <div className={`shrink-0 px-3 py-1.5 rounded-lg border text-center ${getScoreBg(d.composite_score)}`}>
          <p className={`text-lg font-bold ${getScoreColor(d.composite_score)}`}>{d.composite_score.toFixed(1)}</p>
          <p className="text-xs text-gray-500 uppercase">Score</p>
        </div>
      </div>
      <div className="mt-2 flex gap-1 h-1.5 rounded-full overflow-hidden">
        <div className="bg-purple-500 rounded-full" style={{ width: `${d.route_experience_score}%` }} title={`Route Exp: ${d.route_experience_score}`} />
        <div className="bg-emerald-500 rounded-full" style={{ width: `${d.eta_compliance_score}%` }} title={`ETA: ${d.eta_compliance_score}`} />
        <div className="bg-blue-500 rounded-full" style={{ width: `${d.speed_efficiency_score}%` }} title={`Speed: ${d.speed_efficiency_score}`} />
        <div className="bg-amber-500 rounded-full" style={{ width: `${d.consistency_score}%` }} title={`Consistency: ${d.consistency_score}`} />
      </div>
      <div className="mt-1 flex gap-3 text-xs text-gray-500">
        <span className="flex items-center gap-1"><span className="w-1.5 h-1.5 rounded-full bg-purple-500 inline-block" />Route {d.route_experience_score}</span>
        <span className="flex items-center gap-1"><span className="w-1.5 h-1.5 rounded-full bg-emerald-500 inline-block" />ETA {d.eta_compliance_score}</span>
        <span className="flex items-center gap-1"><span className="w-1.5 h-1.5 rounded-full bg-blue-500 inline-block" />Speed {d.speed_efficiency_score}</span>
        <span className="flex items-center gap-1"><span className="w-1.5 h-1.5 rounded-full bg-amber-500 inline-block" />Consistency {d.consistency_score}</span>
      </div>
      <div className="mt-2 flex gap-4 text-xs">
        <span className="text-gray-400">Speed: <span className="text-gray-300">{d.avg_speed_kmph} km/h</span></span>
        <span className="text-gray-400">ETA Rate: <span className={d.eta_success_rate >= 80 ? 'text-emerald-400' : d.eta_success_rate >= 60 ? 'text-amber-400' : 'text-red-400'}>{d.eta_success_rate}%</span></span>
      </div>
    </div>
  );

  return (
    <div className="space-y-4">
      {/* Route summary */}
      <div className="flex items-center gap-3 bg-gray-900 rounded-lg border border-gray-800 p-3">
        <MapPin className="w-4 h-4 text-blue-400 shrink-0" />
        <div className="text-sm">
          <span className="text-white font-medium">{result.origin}</span>
          <span className="text-gray-500 mx-2">→</span>
          <span className="text-white font-medium">{result.destination}</span>
        </div>
        <div className="ml-auto flex gap-4 text-xs text-gray-500">
          <span>{result.total_candidates} candidates</span>
          <span>{result.drivers_with_exact_route_exp ?? result.drivers_with_route_exp} with route exp</span>
        </div>
      </div>

      {/* Experienced on route */}
      {experienced.length > 0 && (
        <div>
          <div className="flex items-center gap-2 mb-2">
            <Star className="w-4 h-4 text-blue-400" />
            <p className="text-sm font-medium text-blue-300">Experienced on This Route ({experienced.length})</p>
          </div>
          <div className="space-y-2 max-h-[300px] overflow-y-auto pr-1">
            {experienced.map((d: any, idx: number) => renderDriverCard(d, idx, true))}
          </div>
        </div>
      )}

      {/* Similar route experience */}
      {similar.length > 0 && (
        <div>
          <div className="flex items-center gap-2 mb-2">
            <MapPin className="w-4 h-4 text-purple-400" />
            <p className="text-sm font-medium text-purple-300">Similar Route Experience ({similar.length})</p>
          </div>
          <div className="space-y-2 max-h-[250px] overflow-y-auto pr-1">
            {similar.map((d: any, idx: number) => renderDriverCard(d, idx, false))}
          </div>
        </div>
      )}

      {/* Fallback: if backend returns old format with just recommended_drivers */}
      {experienced.length === 0 && similar.length === 0 && allDrivers.length > 0 && (
        <div className="space-y-2 max-h-[500px] overflow-y-auto pr-1">
          {allDrivers.map((d: any, idx: number) => renderDriverCard(d, idx, true))}
        </div>
      )}
    </div>
  );
}

// ──────────────────────────────────────────────
// Trip Forecast Result
// ──────────────────────────────────────────────
function ForecastResult({ result }: { result: any }) {
  if (result?.error) return <ErrorCard message={result.error} />;

  const fleet = result?.fleet_forecast;
  const topRoutes = result?.top_routes || {};
  const routeKeys = Object.keys(topRoutes);

  const fleetChartData = (fleet?.next_7_days || []).map((d: any) => ({
    day: d.day_of_week?.slice(0, 3), date: d.date, trips: d.predicted_trips,
  }));

  const trendIcon = fleet?.recent_trend === 'up'
    ? <TrendingUp className="w-4 h-4 text-emerald-400" />
    : <TrendingDown className="w-4 h-4 text-red-400" />;

  return (
    <div className="space-y-5">
      {fleet && (
        <>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            <StatCard label="Total Next Week" value={fleet.total_predicted_week?.toFixed(0)} icon={<Truck className="w-4 h-4 text-blue-400" />} />
            <StatCard label="Avg Daily (Historic)" value={fleet.historical_avg_daily?.toFixed(0)} icon={<TrendingUp className="w-4 h-4 text-purple-400" />} />
            <StatCard label="Recent 7d Avg" value={fleet.recent_avg_daily_7d?.toFixed(0)} icon={<Calendar className="w-4 h-4 text-indigo-400" />} />
            <div className="bg-gray-900 rounded-lg border border-gray-800 p-3 flex items-center gap-2">
              {trendIcon}
              <div>
                <p className="text-xs text-gray-500">Trend</p>
                <p className={`text-sm font-semibold capitalize ${fleet.recent_trend === 'up' ? 'text-emerald-400' : 'text-red-400'}`}>
                  {fleet.recent_trend}
                </p>
              </div>
            </div>
          </div>
          {fleetChartData.length > 0 && (
            <div className="bg-gray-900 rounded-xl border border-gray-800 p-4">
              <p className="text-sm font-medium text-gray-300 mb-3">Fleet-wide Daily Forecast (Next 7 Days)</p>
              <ResponsiveContainer width="100%" height={220}>
                <RechartsBar data={fleetChartData}>
                  <CartesianGrid strokeDasharray="3 3" stroke={tc(tc('#374151'))} />
                  <XAxis dataKey="day" tick={{ fill: tc('#9ca3af'), fontSize: 12 }} />
                  <YAxis tick={{ fill: tc('#9ca3af'), fontSize: 12 }} />
                  <Tooltip contentStyle={{ background: tc('#1f2937'), border: `1px solid ${tc('#374151')}`, borderRadius: 8 }} labelStyle={{ color: tc('#e5e7eb') }}
                    formatter={(val: any) => [`${Number(val).toFixed(0)} trips`, 'Predicted']} />
                  <Bar dataKey="trips" radius={[6, 6, 0, 0]}>
                    {fleetChartData.map((_: any, i: number) => <Cell key={i} fill={BAR_COLORS[i % BAR_COLORS.length]} />)}
                  </Bar>
                </RechartsBar>
              </ResponsiveContainer>
            </div>
          )}
        </>
      )}
      {routeKeys.length > 0 && (
        <div className="bg-gray-900 rounded-xl border border-gray-800 p-4">
          <p className="text-sm font-medium text-gray-300 mb-3">Top Route Forecasts</p>
          <div className="space-y-2 max-h-[350px] overflow-y-auto pr-1">
            {routeKeys.map(route => {
              const r = topRoutes[route];
              const trendColor = r.recent_trend === 'up' ? 'text-emerald-400' : r.recent_trend === 'down' ? 'text-red-400' : 'text-gray-400';
              return (
                <div key={route} className="flex items-center gap-3 bg-gray-800/50 rounded-lg border border-gray-700/50 px-3 py-2">
                  <MapPin className="w-3.5 h-3.5 text-blue-400 shrink-0" />
                  <div className="min-w-0 flex-1">
                    <p className="text-sm text-white truncate">{route}</p>
                    <p className="text-xs text-gray-500">Avg {r.historical_avg_daily}/day</p>
                  </div>
                  <div className="text-right shrink-0">
                    <p className="text-sm font-bold text-blue-400">{r.total_predicted_week?.toFixed(0)}</p>
                    <p className="text-xs text-gray-500">next week</p>
                  </div>
                  <span className={`text-xs font-medium capitalize ${trendColor}`}>{r.recent_trend}</span>
                </div>
              );
            })}
          </div>
        </div>
      )}
      {result?.generated_at && (
        <p className="text-xs text-gray-600 text-right">Generated: {new Date(result.generated_at).toLocaleString()}</p>
      )}
    </div>
  );
}

// ──────────────────────────────────────────────
// Client Forecast Panel
// ──────────────────────────────────────────────
function ClientForecastPanel() {
  const [clientName, setClientName] = useState('');
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<any>(null);

  const handleForecast = async () => {
    setLoading(true);
    setResult(null);
    try {
      const res = await getClientForecast(clientName || undefined);
      setResult(res.data);
    } catch (err: any) {
      setResult({ error: err?.response?.data?.detail || err.message || 'Forecast failed' });
    } finally {
      setLoading(false);
    }
  };

  const forecast = result?.forecast;
  const chartData = (forecast?.next_7_days || []).map((d: any) => ({
    day: d.day_of_week?.slice(0, 3), date: d.date, trips: d.predicted_trips,
  }));

  const trendColors: Record<string, string> = { growing: 'text-emerald-400', declining: 'text-red-400', stable: 'text-amber-400' };

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3">
        <div className="flex-1">
          <label className="block text-xs text-gray-400 mb-1">Client / Company Name</label>
          <div className="relative">
            <Search className="w-4 h-4 text-gray-500 absolute left-3 top-1/2 -translate-y-1/2" />
            <input
              type="text" value={clientName}
              onChange={e => setClientName(e.target.value)}
              placeholder="e.g. TATA STEEL (leave empty for all)"
              className="w-full pl-10 pr-3 py-2 bg-gray-800 border border-gray-700 rounded-lg text-sm text-gray-200 focus:outline-none focus:border-blue-500"
            />
          </div>
        </div>
        <button onClick={handleForecast} disabled={loading}
          className="flex items-center gap-2 px-6 py-2.5 bg-blue-600 hover:bg-blue-700 text-white rounded-lg text-sm font-medium disabled:opacity-50 transition-colors mt-5">
          {loading ? <div className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" /> : <TrendingUp className="w-4 h-4" />}
          {loading ? 'Loading...' : 'Get Forecast'}
        </button>
      </div>

      {loading && !result && <Spinner />}

      {result && (
        result.error ? <ErrorCard message={result.error} /> :
        result.matches ? (
          // Multiple matches
          <div className="bg-gray-900 rounded-lg border border-gray-800 p-4">
            <p className="text-sm text-amber-400 mb-2">Multiple clients match "{clientName}":</p>
            <div className="flex flex-wrap gap-2">
              {result.matches.map((m: string) => (
                <button key={m} onClick={() => { setClientName(m); }}
                  className="px-3 py-1 bg-gray-800 hover:bg-gray-700 text-sm text-gray-300 rounded-lg border border-gray-700">
                  {m}
                </button>
              ))}
            </div>
          </div>
        ) : forecast ? (
          // Single client forecast
          <div className="space-y-4">
            {result.client && (
              <div className="bg-gray-900 rounded-lg border border-gray-800 p-3 flex items-center gap-3">
                <Building2 className="w-5 h-5 text-blue-400" />
                <p className="text-white font-semibold">{result.client}</p>
                <span className={`ml-auto text-sm font-medium capitalize ${trendColors[forecast.trend] || 'text-gray-400'}`}>
                  {forecast.trend} {forecast.growth_pct_30d > 0 ? '+' : ''}{forecast.growth_pct_30d}%
                </span>
              </div>
            )}

            <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
              <StatCard label="Predicted Next Week" value={forecast.total_predicted_week?.toFixed(0)} icon={<TrendingUp className="w-4 h-4 text-blue-400" />} />
              <StatCard label="Avg Daily (Historic)" value={forecast.historical_avg_daily?.toFixed(0)} icon={<Calendar className="w-4 h-4 text-purple-400" />} />
              <StatCard label="Recent 7d Avg" value={forecast.recent_avg_daily_7d?.toFixed(1)} icon={<Truck className="w-4 h-4 text-indigo-400" />} />
              <StatCard label="Total Historical" value={forecast.total_historical_trips?.toLocaleString()} icon={<Building2 className="w-4 h-4 text-cyan-400" />} />
            </div>

            {chartData.length > 0 && (
              <div className="bg-gray-900 rounded-xl border border-gray-800 p-4">
                <p className="text-sm font-medium text-gray-300 mb-3">7-Day Demand Forecast</p>
                <ResponsiveContainer width="100%" height={220}>
                  <RechartsBar data={chartData}>
                    <CartesianGrid strokeDasharray="3 3" stroke={tc(tc('#374151'))} />
                    <XAxis dataKey="day" tick={{ fill: tc('#9ca3af'), fontSize: 12 }} />
                    <YAxis tick={{ fill: tc('#9ca3af'), fontSize: 12 }} />
                    <Tooltip contentStyle={{ background: tc('#1f2937'), border: `1px solid ${tc('#374151')}`, borderRadius: 8 }} labelStyle={{ color: tc('#e5e7eb') }}
                      formatter={(val: any) => [`${Number(val).toFixed(0)} trips`, 'Predicted']} />
                    <Bar dataKey="trips" radius={[6, 6, 0, 0]}>
                      {chartData.map((_: any, i: number) => <Cell key={i} fill={BAR_COLORS[i % BAR_COLORS.length]} />)}
                    </Bar>
                  </RechartsBar>
                </ResponsiveContainer>
              </div>
            )}
          </div>
        ) : result.clients ? (
          // All clients summary
          <div className="space-y-3">
            <p className="text-sm text-gray-400">{result.clients_count} clients with forecasts</p>
            <div className="space-y-2 max-h-[500px] overflow-y-auto pr-1">
              {result.clients.slice(0, 30).map((c: any) => (
                <div key={c.client}
                  onClick={() => setClientName(c.client)}
                  className="flex items-center gap-3 bg-gray-900 rounded-lg border border-gray-800 px-3 py-2 cursor-pointer hover:border-gray-600 transition-colors">
                  <Building2 className="w-4 h-4 text-blue-400 shrink-0" />
                  <div className="min-w-0 flex-1">
                    <p className="text-sm text-white truncate">{c.client}</p>
                    <p className="text-xs text-gray-500">Avg {c.avg_daily}/day</p>
                  </div>
                  <div className="text-right shrink-0">
                    <p className="text-sm font-bold text-blue-400">{c.predicted_week?.toFixed(0)}</p>
                    <p className="text-xs text-gray-500">next week</p>
                  </div>
                  <span className={`text-xs font-medium capitalize ${trendColors[c.trend] || 'text-gray-400'}`}>{c.trend}</span>
                </div>
              ))}
            </div>
          </div>
        ) : null
      )}
    </div>
  );
}

// ──────────────────────────────────────────────
// Main MLInsights Page
// ──────────────────────────────────────────────
export default function MLInsights() {
  const { data: models, loading: modelsLoading, refetch } = useApi<MLModel[]>(() => listModels());
  const [activeTab, setActiveTab] = useState<TabKey>('eta');
  const [predLoading, setPredLoading] = useState(false);
  const [result, setResult] = useState<any>(null);
  const [lastTripStart, setLastTripStart] = useState<string>('');
  const [trainLoading, setTrainLoading] = useState(false);
  const [tierLoading, setTierLoading] = useState<string | null>(null);
  const [cacheLoading, setCacheLoading] = useState(false);

  const handlePredict = async (values: Record<string, any>) => {
    setPredLoading(true);
    setResult(null);
    try {
      let res;
      if (activeTab === 'eta') {
        setLastTripStart(values.trip_start || '');
        res = await predictEta(values);
      } else if (activeTab === 'sla') {
        res = await predictSla(values);
      } else if (activeTab === 'recommend') {
        res = await recommendDrivers({ origin: values.origin, destination: values.destination, top_n: Number(values.top_n) || 10 });
      } else {
        res = await forecastTrips();
      }
      setResult(res.data);
    } catch (err: any) {
      setResult({ error: err?.response?.data?.detail || err.message || 'Prediction failed' });
    } finally {
      setPredLoading(false);
    }
  };

  const handleTrainAll = async () => {
    setTrainLoading(true);
    try { await trainAllModels(); refetch(); } catch { /* ignore */ }
    setTrainLoading(false);
  };

  const handleTrainTier = async (tier: string) => {
    setTierLoading(tier);
    try { await trainTier(tier); refetch(); } catch { /* ignore */ }
    setTierLoading(null);
  };

  const handleClearCache = async () => {
    setCacheLoading(true);
    try { await clearModelCache(); } catch { /* ignore */ }
    setCacheLoading(false);
  };

  const getFields = () => {
    switch (activeTab) {
      case 'eta': return ETA_FIELDS;
      case 'sla': return SLA_FIELDS;
      case 'recommend': return RECOMMEND_FIELDS;
      case 'forecast': return [];
      default: return ETA_FIELDS;
    }
  };

  const getResultRenderer = () => {
    switch (activeTab) {
      case 'eta': return (r: any) => <ETAResult result={r} tripStart={lastTripStart} />;
      case 'sla': return (r: any) => <SLAResult result={r} />;
      case 'recommend': return (r: any) => <RecommenderResult result={r} />;
      default: return undefined;
    }
  };

  const modelsList = Array.isArray(models) ? models : (models as any)?.data || [];

  return (
    <PageContainer>
      {/* Trained Models Section */}
      <div className="mb-6">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-lg font-semibold text-white">Trained Models</h2>
          <div className="flex gap-2 flex-wrap">
            {['daily', 'weekly', 'monthly'].map(tier => (
              <button key={tier} onClick={() => handleTrainTier(tier)} disabled={tierLoading !== null}
                className="flex items-center gap-1.5 px-3 py-2 bg-indigo-700 hover:bg-indigo-600 text-white rounded-lg text-xs font-medium disabled:opacity-50 transition-colors capitalize">
                {tierLoading === tier ? <div className="w-3 h-3 border-2 border-white/30 border-t-white rounded-full animate-spin" /> : <Play className="w-3 h-3" />}
                {tier}
              </button>
            ))}
            <button onClick={handleTrainAll} disabled={trainLoading}
              className="flex items-center gap-2 px-4 py-2 bg-purple-600 hover:bg-purple-700 text-white rounded-lg text-sm font-medium disabled:opacity-50 transition-colors">
              {trainLoading ? <div className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" /> : <Play className="w-4 h-4" />}
              Train All
            </button>
            <button onClick={handleClearCache} disabled={cacheLoading}
              className="flex items-center gap-2 px-4 py-2 bg-gray-700 hover:bg-gray-600 text-white rounded-lg text-sm font-medium disabled:opacity-50 transition-colors">
              <Trash2 className="w-4 h-4" />
              Clear Cache
            </button>
          </div>
        </div>
        {modelsLoading ? <Spinner /> : modelsList.length > 0 ? (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {modelsList.map((m: MLModel) => <ModelCard key={m.id} model={m} />)}
          </div>
        ) : (
          <p className="text-gray-500 text-sm">No models trained yet. Click "Train All" to get started.</p>
        )}
      </div>

      {/* Predictions Section */}
      <div>
        <h2 className="text-lg font-semibold text-white mb-4">Predictions & Monitoring</h2>
        <div className="bg-gray-800 rounded-lg p-1 flex gap-1 mb-6 w-fit flex-wrap">
          {TABS.map(tab => {
            const Icon = tab.icon;
            return (
              <button key={tab.key} onClick={() => { setActiveTab(tab.key); setResult(null); }}
                className={`flex items-center gap-1.5 px-3 py-2 rounded-md text-sm font-medium transition-colors ${activeTab === tab.key ? 'bg-blue-600 text-white' : 'text-gray-400 hover:text-gray-200'}`}>
                <Icon className="w-3.5 h-3.5" />
                {tab.label}
              </button>
            );
          })}
        </div>

        {/* Anomaly scanner — standalone panel */}
        {activeTab === 'anomaly' && <AnomalyScanPanel />}

        {/* Fatigue monitor — standalone panel */}
        {activeTab === 'fatigue' && <FatiguePanel />}

        {/* Client forecast — standalone panel */}
        {activeTab === 'client' && <ClientForecastPanel />}

        {/* Trip forecast — button-only */}
        {activeTab === 'forecast' && (
          <div>
            <button onClick={() => handlePredict({})} disabled={predLoading}
              className="flex items-center gap-2 px-6 py-3 bg-blue-600 hover:bg-blue-700 text-white rounded-lg text-sm font-medium disabled:opacity-50 transition-colors mb-4">
              {predLoading ? <div className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" /> : <TrendingUp className="w-4 h-4" />}
              {predLoading ? 'Loading...' : 'Get Trip Forecast'}
            </button>
            {predLoading && !result && <Spinner />}
            {result && <ForecastResult result={result} />}
          </div>
        )}

        {/* Form-based tabs: ETA, SLA, Recommender */}
        {['eta', 'sla', 'recommend'].includes(activeTab) && (
          <PredictionForm
            fields={getFields()}
            onSubmit={handlePredict}
            loading={predLoading}
            result={result}
            renderResult={getResultRenderer()}
            submitLabel={
              activeTab === 'recommend' ? 'Find Drivers' :
              activeTab === 'sla' ? 'Predict SLA' :
              'Predict ETA'
            }
          />
        )}
      </div>
    </PageContainer>
  );
}
