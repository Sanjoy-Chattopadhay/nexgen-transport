import { useState } from 'react';
import { useParams, Link } from 'react-router-dom';
import {
  ArrowLeft, Route, Clock, Gauge, AlertTriangle, Satellite, Timer, IndianRupee,
  Truck, PackageOpen, Warehouse, Flag, Zap, MapPin, TrendingUp, Coffee,
  ChevronDown, ChevronUp,
} from 'lucide-react';
import {
  ResponsiveContainer, PieChart, Pie, Cell, Tooltip, ComposedChart, Bar, Line,
  XAxis, YAxis, CartesianGrid, Legend,
} from 'recharts';
import PageContainer from '../components/layout/PageContainer';
import Spinner from '../components/ui/Spinner';
import Badge from '../components/ui/Badge';
import DataTable from '../components/ui/DataTable';
import AreaChart from '../components/charts/AreaChart';
import BarChart from '../components/charts/BarChart';
import HeatmapGrid from '../components/charts/HeatmapGrid';
import LeafletTrackMap from '../components/tta/LeafletTrackMap';
import WeatherImpactSection from '../components/tta/WeatherImpactSection';
import CostConfigPanel from '../components/tta/CostConfigPanel';
import PlantDelaySection from '../components/tta/PlantDelaySection';
import { useApi } from '../hooks/useApi';
import { getTTATripAnalysis, getTTATripGps } from '../services/tta';
import { formatNumber, formatDuration, formatDateTime } from '../lib/formatters';
import { CHART_COLORS } from '../lib/colors';
import { tc } from '../../../core/theme';

const TOOLTIP_STYLE = { backgroundColor: CHART_COLORS.tooltipBg, border: `1px solid ${tc('#374151')}`, borderRadius: 8, color: tc('#f3f4f6') };

const PHASE_STYLE: Record<string, { bar: string; icon: any; text: string }> = {
  loading: { bar: 'bg-amber-500', icon: Warehouse, text: 'text-amber-400' },
  transit: { bar: 'bg-blue-500', icon: Truck, text: 'text-blue-400' },
  unloading: { bar: 'bg-purple-500', icon: PackageOpen, text: 'text-purple-400' },
  closure: { bar: 'bg-gray-500', icon: Flag, text: 'text-gray-400' },
};

const STOP_COLORS: Record<string, string> = {
  'Unloading / Detention': tc('#a855f7'),
  'Loading / At plant': tc('#f59e0b'),
  'Night rest': '#6366f1',
  'Long halt': tc('#ef4444'),
  'Extended halt': '#f97316',
  'Lunch break': tc('#10b981'),
  'Dinner break': '#14b8a6',
  'Tea / short break': tc('#06b6d4'),
  'Halt': tc('#6b7280'),
};

function shortTime(iso: string | null | undefined): string {
  if (!iso) return '-';
  const d = new Date(iso);
  return d.toLocaleString('en-IN', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' });
}

function Section({ icon: Icon, title, subtitle, children }: { icon: any; title: string; subtitle?: string; children: React.ReactNode }) {
  return (
    <div className="bg-gray-900 rounded-xl border border-gray-800 p-5 mb-6">
      <div className="mb-4">
        <h2 className="text-lg font-semibold text-white flex items-center gap-2">
          <Icon className="w-5 h-5 text-blue-400" /> {title}
        </h2>
        {subtitle && <p className="text-xs text-gray-500 mt-1">{subtitle}</p>}
      </div>
      {children}
    </div>
  );
}

function Stat({ label, value, sub, accent = 'text-gray-100' }: { label: string; value: React.ReactNode; sub?: string; accent?: string }) {
  return (
    <div className="bg-gray-800/50 rounded-lg p-3.5">
      <p className="text-xs text-gray-500 mb-1">{label}</p>
      <p className={`text-xl font-bold ${accent}`}>{value}</p>
      {sub && <p className="text-xs text-gray-500 mt-0.5">{sub}</p>}
    </div>
  );
}

function ScoreRing({ score }: { score: number }) {
  const color = score >= 80 ? '#22c55e' : score >= 60 ? tc('#f59e0b') : tc('#ef4444');
  const C = 2 * Math.PI * 44;
  return (
    <div className="relative w-32 h-32">
      <svg viewBox="0 0 100 100" className="w-full h-full -rotate-90">
        <circle cx="50" cy="50" r="44" fill="none" stroke={tc(tc('#1f2937'))} strokeWidth="10" />
        <circle cx="50" cy="50" r="44" fill="none" stroke={color} strokeWidth="10" strokeLinecap="round"
          strokeDasharray={`${(score / 100) * C} ${C}`} />
      </svg>
      <div className="absolute inset-0 flex flex-col items-center justify-center">
        <span className="text-3xl font-bold text-white">{score}</span>
        <span className="text-xs text-gray-500 uppercase tracking-wider">/ 100</span>
      </div>
    </div>
  );
}

export default function TTATripAnalysis() {
  const { tripNo } = useParams<{ tripNo: string }>();
  const { data: a, loading, error, refetch } = useApi(() => getTTATripAnalysis(tripNo!), [tripNo]);
  const { data: gps } = useApi(() => getTTATripGps(tripNo!, 1500), [tripNo]);
  const [showAllStops, setShowAllStops] = useState(false);

  if (loading) return <PageContainer title={`Trip ${tripNo} — Analysis`}><Spinner /></PageContainer>;
  if (error || !a) return (
    <PageContainer title={`Trip ${tripNo} — Analysis`}>
      <p className="text-red-400">{error || 'No analysis available'}</p>
    </PageContainer>
  );

  const ov = a.overview;
  const k = ov.kpis;
  const early = (ov.delivery_delta_min ?? 0) < 0;

  const movementDonut = [
    { name: 'Moving', value: Math.round(k.moving_min), color: tc('#3b82f6') },
    { name: 'Stopped', value: Math.round(k.stopped_min), color: tc('#f59e0b') },
  ];
  const zoneData = a.speed.zones ? [
    { zone: '< 20 slow', pct: a.speed.zones.slow_pct },
    { zone: '20–40', pct: a.speed.zones.moderate_pct },
    { zone: '40–60', pct: a.speed.zones.normal_pct },
    { zone: '≥ 60 high', pct: a.speed.zones.high_pct },
  ] : [];
  const stopDonut = (a.stops.categories as any[]).map(c => ({
    name: c.reason, value: Math.round(c.total_min), color: STOP_COLORS[c.reason] || tc('#6b7280'),
  }));
  const costDonut = [
    { name: 'Fuel (moving)', value: Math.round(a.cost.moving_fuel_liters * a.cost.params.fuel_price_per_liter), color: tc('#3b82f6') },
    { name: 'Fuel (idling)', value: a.cost.idle_waste_inr, color: tc('#f59e0b') },
    { name: 'Driver', value: a.cost.driver_cost_inr, color: '#8b5cf6' },
  ];

  return (
    <PageContainer title={`Trip ${ov.trip_no} — GPS Analysis Report`}>
      {/* Header */}
      <div className="flex items-center justify-between mb-4 flex-wrap gap-2">
        <Link to={`/trips/${ov.trip_no}`} className="inline-flex items-center gap-1.5 text-sm text-gray-400 hover:text-gray-200">
          <ArrowLeft className="w-4 h-4" /> Trip Detail
        </Link>
        <div className="flex items-center gap-2 flex-wrap">
          {ov.status && <Badge label={ov.status} variant="success" />}
          {ov.delivery_status && <Badge label={ov.delivery_status} variant={early || (ov.delivery_status || '').toLowerCase().includes('on time') ? 'success' : 'danger'} />}
          {ov.delivery_delta_min != null && (
            <Badge label={`${early ? 'Early' : 'Late'} by ${formatDuration(Math.abs(ov.delivery_delta_min))}`} variant={early ? 'success' : 'danger'} />
          )}
        </div>
      </div>

      {/* Hero */}
      <div className="bg-gradient-to-r from-blue-950/60 to-gray-900 rounded-xl border border-gray-800 p-5 mb-6">
        <div className="flex items-start justify-between flex-wrap gap-4">
          <div>
            <h1 className="text-xl font-bold text-white flex items-center gap-2">
              <MapPin className="w-5 h-5 text-blue-400" /> {ov.route}
            </h1>
            <p className="text-sm text-gray-400 mt-1">
              {ov.vehicle} · {ov.driver} · {ov.transporter}
            </p>
            <p className="text-xs text-gray-500 mt-0.5">
              {ov.consignor} → {ov.consignee} · GPS window {shortTime(k.first_ping)} → {shortTime(k.last_ping)}
            </p>
          </div>
          <div className="text-right">
            <p className="text-xs text-gray-500">GPS distance vs declared</p>
            <p className="text-lg font-bold text-gray-100">
              {k.distance_km} km <span className="text-xs text-gray-500">vs {k.distance_declared_km} km</span>
            </p>
            <p className={`text-xs ${Math.abs(k.gps_vs_declared_km ?? 0) <= 5 ? 'text-emerald-400' : 'text-amber-400'}`}>
              Δ {k.gps_vs_declared_km} km — {Math.abs(k.gps_vs_declared_km ?? 0) <= 5 ? 'GPS confirms declared distance' : 'variance to review'}
            </p>
          </div>
        </div>
        <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-8 gap-3 mt-5">
          <Stat label="Distance" value={`${formatNumber(Math.round(k.distance_km))} km`} accent="text-blue-400" />
          <Stat label="Transit Time" value={formatDuration(k.transit_time_min)} accent="text-purple-400" />
          <Stat label="Utilization" value={`${k.utilization_pct}%`} sub="moving share of GPS time" accent={k.utilization_pct >= 50 ? 'text-emerald-400' : 'text-amber-400'} />
          <Stat label="Avg Moving Speed" value={`${k.avg_moving_speed} km/h`} accent="text-cyan-400" />
          <Stat label="Detention" value={formatDuration(k.detention_min)} sub="at destination" accent="text-amber-400" />
          <Stat label="Stops" value={k.total_stops} accent="text-gray-100" />
          <Stat label="Driving Score" value={k.driving_score} sub="0–100" accent={k.driving_score >= 80 ? 'text-emerald-400' : k.driving_score >= 60 ? 'text-amber-400' : 'text-red-400'} />
          <Stat label="Est. Trip Cost" value={`₹${formatNumber(a.cost.total_cost_inr)}`} sub={`₹${a.cost.cost_per_km}/km`} accent="text-gray-100" />
        </div>
      </div>

      {/* 1. Trip Lifecycle */}
      <Section icon={Timer} title="Trip Lifecycle"
        subtitle="Booking → Dispatch → Arrival → Unloading → Closure — GPS activity analysed inside each window">
        <div className="flex w-full h-4 rounded-full overflow-hidden mb-5 border border-gray-800">
          {(a.phases as any[]).map(p => (
            p.share_pct > 0 && (
              <div key={p.key} className={`${PHASE_STYLE[p.key].bar} h-full`} style={{ width: `${p.share_pct}%` }}
                title={`${p.label}: ${formatDuration(p.duration_min)} (${p.share_pct}%)`} />
            )
          ))}
        </div>
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
          {(a.phases as any[]).map(p => {
            const st = PHASE_STYLE[p.key];
            return (
              <div key={p.key} className="bg-gray-800/50 rounded-lg p-4 border border-gray-800">
                <div className="flex items-center gap-2 mb-1">
                  <st.icon className={`w-4 h-4 ${st.text}`} />
                  <h3 className={`text-sm font-semibold ${st.text}`}>{p.label}</h3>
                </div>
                <p className="text-xs text-gray-500 mb-2">{p.description}</p>
                <p className="text-2xl font-bold text-white">{p.duration_min != null ? formatDuration(p.duration_min) : '—'}
                  <span className="text-xs font-normal text-gray-500 ml-2">{p.share_pct}%</span>
                </p>
                <p className="text-xs text-gray-500 mb-2">{shortTime(p.from_ts)} → {shortTime(p.to_ts)}</p>
                {p.gps.has_gps ? (
                  <div className="text-xs text-gray-400 space-y-0.5 border-t border-gray-800 pt-2">
                    <p>{p.gps.distance_km} km · {p.gps.pings} pings</p>
                    <p>moving {formatDuration(p.gps.moving_min)} · stopped {formatDuration(p.gps.stopped_min)}</p>
                    <p>utilization {p.gps.utilization_pct}% · avg {p.gps.avg_moving_speed} km/h</p>
                  </div>
                ) : (
                  <p className="text-xs text-gray-600 border-t border-gray-800 pt-2">no GPS pings in this window</p>
                )}
                {p.anomaly && <p className="text-xs text-amber-400 mt-2">⚠ {p.anomaly}</p>}
              </div>
            );
          })}
        </div>
      </Section>

      {/* 1b. In-Plant Delay (GPS-reconstructed) */}
      <PlantDelaySection tripNo={ov.trip_no} />

      {/* 2. Movement & Utilization */}
      <Section icon={TrendingUp} title="Movement & Utilization" subtitle="Where the clock actually went — moving vs standing, day by day">
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          <div>
            <ResponsiveContainer width="100%" height={220}>
              <PieChart>
                <Pie data={movementDonut} dataKey="value" nameKey="name" innerRadius={55} outerRadius={85} paddingAngle={2}>
                  {movementDonut.map(d => <Cell key={d.name} fill={d.color} />)}
                </Pie>
                <Tooltip contentStyle={TOOLTIP_STYLE} formatter={(v: any) => formatDuration(v)} />
                <Legend wrapperStyle={{ fontSize: 12 }} />
              </PieChart>
            </ResponsiveContainer>
            <p className="text-center text-xs text-gray-500 -mt-2">GPS time split (minutes)</p>
          </div>
          <div className="lg:col-span-2">
            <ResponsiveContainer width="100%" height={240}>
              <ComposedChart data={a.driving.daily}>
                <CartesianGrid strokeDasharray="3 3" stroke={CHART_COLORS.grid} />
                <XAxis dataKey="date" tick={{ fill: tc('#9ca3af'), fontSize: 12 }} axisLine={false} tickLine={false} />
                <YAxis yAxisId="h" tick={{ fill: tc('#9ca3af'), fontSize: 12 }} axisLine={false} tickLine={false} label={{ value: 'hours', angle: -90, fill: tc('#6b7280'), fontSize: 12 }} />
                <YAxis yAxisId="km" orientation="right" tick={{ fill: tc('#9ca3af'), fontSize: 12 }} axisLine={false} tickLine={false} label={{ value: 'km', angle: 90, fill: tc('#6b7280'), fontSize: 12 }} />
                <Tooltip contentStyle={TOOLTIP_STYLE} />
                <Legend wrapperStyle={{ fontSize: 12 }} />
                <Bar yAxisId="h" dataKey="drive_hours" name="Drive hours" fill={tc(tc('#3b82f6'))} radius={[4, 4, 0, 0]} />
                <Bar yAxisId="h" dataKey="idle_hours" name="Idle hours" fill={tc(tc('#f59e0b'))} radius={[4, 4, 0, 0]} />
                <Line yAxisId="km" type="monotone" dataKey="distance_km" name="Distance (km)" stroke="#22c55e" strokeWidth={2} dot />
              </ComposedChart>
            </ResponsiveContainer>
            <p className="text-center text-xs text-gray-500">Daily rhythm — drive vs idle hours with distance covered</p>
          </div>
        </div>
      </Section>

      {/* 3. Speed Intelligence */}
      <Section icon={Gauge} title="Speed Intelligence"
        subtitle={`Distribution of moving speeds, consistency, and segments above ${a.speed.kpis.overspeed_threshold} km/h`}>
        <div className="grid grid-cols-2 md:grid-cols-5 gap-3 mb-5">
          <Stat label="Avg Moving" value={`${a.speed.kpis.avg_moving_speed} km/h`} accent="text-cyan-400" />
          <Stat label="Max Speed" value={`${a.speed.kpis.max_speed} km/h`} accent="text-blue-400" />
          <Stat label="Consistency" value={a.speed.kpis.consistency} sub={`σ = ${a.speed.kpis.speed_std_dev}`} accent={a.speed.kpis.consistency === 'High' ? 'text-emerald-400' : 'text-amber-400'} />
          <Stat label="Over-speed Pings" value={a.speed.kpis.overspeed_pings} sub={`${a.speed.kpis.overspeed_pct}% of pings`} accent={a.speed.kpis.overspeed_pings > 0 ? 'text-red-400' : 'text-emerald-400'} />
          <Stat label="Provider Violations" value={formatNumber(k.speed_violations_declared)} sub="declared by TTA" accent="text-amber-400" />
        </div>
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          <div>
            <BarChart data={a.speed.histogram} xKey="band" series={[{ key: 'count', color: tc('#3b82f6'), label: 'Pings' }]} height={240} />
            <p className="text-center text-xs text-gray-500">Speed distribution — moving pings per 10 km/h band</p>
          </div>
          <div>
            <BarChart data={zoneData} xKey="zone" series={[{ key: 'pct', color: '#8b5cf6', label: '% of moving time' }]} height={240} />
            <p className="text-center text-xs text-gray-500">Speed zones (share of moving pings)</p>
          </div>
        </div>
        {a.speed.overspeed_segments.length > 0 && (
          <div className="mt-5">
            <h3 className="text-sm font-semibold text-gray-300 mb-2">Over-speed segments</h3>
            <DataTable columns={[
              { key: 'start', label: 'From', render: (r: any) => shortTime(r.start) },
              { key: 'end', label: 'To', render: (r: any) => shortTime(r.end) },
              { key: 'minutes', label: 'Duration', render: (r: any) => `${r.minutes} min` },
              { key: 'peak_kph', label: 'Peak', render: (r: any) => <span className="text-red-400 font-semibold">{r.peak_kph} km/h</span> },
              { key: 'near', label: 'Near', render: (r: any) => r.near ?? '-' },
            ]} data={a.speed.overspeed_segments} />
          </div>
        )}
      </Section>

      {/* 3b. Weather Impact (on demand) */}
      <WeatherImpactSection tripNo={ov.trip_no} />

      {/* 4. Stoppage Analysis */}
      <Section icon={Coffee} title="Stoppage Analysis"
        subtitle="Every standstill ≥ 5 min, classified by business phase (loading / unloading) and driver-behaviour taxonomy">
        <div className="grid grid-cols-2 md:grid-cols-5 gap-3 mb-5">
          <Stat label="Total Stops" value={a.stops.kpis.total_stops} />
          <Stat label="Stopped Hours" value={`${a.stops.kpis.total_stop_hours} h`} accent="text-amber-400" />
          <Stat label="Longest Stop" value={formatDuration(a.stops.kpis.longest_stop_min)} sub={a.stops.kpis.longest_stop_where ?? ''} accent="text-red-400" />
          <Stat label="Distinct Places" value={a.stops.kpis.distinct_places} />
          <Stat label="Unloading Share" value={`${(a.stops.categories as any[]).find((c: any) => c.reason === 'Unloading / Detention')?.share_pct ?? 0}%`} sub="of stopped time" accent="text-purple-400" />
        </div>
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          <div>
            <ResponsiveContainer width="100%" height={240}>
              <PieChart>
                <Pie data={stopDonut} dataKey="value" nameKey="name" innerRadius={50} outerRadius={85} paddingAngle={2}>
                  {stopDonut.map(d => <Cell key={d.name} fill={d.color} />)}
                </Pie>
                <Tooltip contentStyle={TOOLTIP_STYLE} formatter={(v: any) => formatDuration(v)} />
                <Legend wrapperStyle={{ fontSize: 12 }} />
              </PieChart>
            </ResponsiveContainer>
            <p className="text-center text-xs text-gray-500 -mt-2">Stopped time by reason</p>
          </div>
          <div className="space-y-2">
            {(a.stops.categories as any[]).map((c: any) => (
              <div key={c.reason} className="flex items-center justify-between bg-gray-800/50 rounded-lg px-3 py-2">
                <div className="flex items-center gap-2">
                  <span className="w-2.5 h-2.5 rounded-full inline-block" style={{ background: STOP_COLORS[c.reason] || tc('#6b7280') }} />
                  <div>
                    <p className="text-sm text-gray-200">{c.reason}</p>
                    <p className="text-xs text-gray-500">{c.rule}</p>
                  </div>
                </div>
                <div className="text-right">
                  <p className="text-sm font-semibold text-gray-100">{formatDuration(c.total_min)}</p>
                  <p className="text-xs text-gray-500">{c.count}× · longest {formatDuration(c.longest_min)}</p>
                </div>
              </div>
            ))}
          </div>
        </div>
        <div className="mt-5">
          <div className="flex items-center justify-between mb-2">
            <h3 className="text-sm font-semibold text-gray-300">Stop events</h3>
            <span className="text-xs text-gray-500">{a.stops.events.length} total</span>
          </div>
          <DataTable columns={[
            { key: 'start', label: 'From', render: (r: any) => shortTime(r.start) },
            { key: 'minutes', label: 'Duration', render: (r: any) => <span className="font-semibold">{formatDuration(r.minutes)}</span> },
            { key: 'reason', label: 'Classified As', render: (r: any) => (
              <span className="px-2 py-0.5 rounded-full text-xs font-medium" style={{ background: `${STOP_COLORS[r.reason] || tc('#6b7280')}26`, color: STOP_COLORS[r.reason] || tc('#9ca3af') }}>{r.reason}</span>
            ) },
            { key: 'near', label: 'Location', render: (r: any) => <span className="text-gray-400 text-xs">{r.near ?? '-'}{r.state ? ` (${r.state})` : ''}</span> },
            { key: 'rule', label: 'Rule', render: (r: any) => <span className="text-gray-500 text-xs">{r.rule}</span> },
          ]} data={showAllStops ? a.stops.events : a.stops.events.slice(0, 6)} />
          {a.stops.events.length > 6 && (
            <button onClick={() => setShowAllStops(v => !v)}
              className="mt-2.5 inline-flex items-center gap-1 text-xs text-blue-400 hover:text-blue-300 font-medium">
              {showAllStops
                ? <><ChevronUp className="w-3.5 h-3.5" /> Show less</>
                : <><ChevronDown className="w-3.5 h-3.5" /> See all {a.stops.events.length} stop events</>}
            </button>
          )}
        </div>
      </Section>

      {/* 5. Driving Pattern */}
      <Section icon={Zap} title="Driving Pattern & Safety"
        subtitle="Hour-of-day rhythm, harsh events, night exposure and a 0–100 driving style score">
        <div className="flex flex-col lg:flex-row gap-6 mb-5">
          <div className="flex flex-col items-center justify-center shrink-0">
            <ScoreRing score={a.driving.score} />
            <p className="text-xs text-gray-500 mt-2">Driving Score</p>
          </div>
          <div className="grid grid-cols-2 md:grid-cols-3 gap-3 flex-1">
            <Stat label="Harsh Acceleration" value={a.driving.kpis.harsh_accel} sub={`Δ > 25 km/h between pings`} accent="text-amber-400" />
            <Stat label="Harsh Braking" value={a.driving.kpis.harsh_brake} accent="text-red-400" />
            <Stat label="Night Driving" value={`${a.driving.kpis.night_driving_pct}%`} sub="22:00–04:59 share of moving" accent={a.driving.kpis.night_driving_pct > 40 ? 'text-red-400' : 'text-gray-100'} />
            <Stat label="Longest Continuous Drive" value={formatDuration(a.driving.kpis.longest_continuous_drive_min)} sub="fatigue indicator" />
            <Stat label="Peak Driving Hour" value={`${a.driving.kpis.peak_hour}:00`} />
            <Stat label="Harsh / 100 pings" value={a.driving.kpis.harsh_per_100_pings} />
          </div>
        </div>
        <div className="mb-5">
          <ResponsiveContainer width="100%" height={240}>
            <ComposedChart data={a.driving.by_hour}>
              <CartesianGrid strokeDasharray="3 3" stroke={CHART_COLORS.grid} />
              <XAxis dataKey="hour" tick={{ fill: tc('#9ca3af'), fontSize: 12 }} axisLine={false} tickLine={false} />
              <YAxis yAxisId="pct" tick={{ fill: tc('#9ca3af'), fontSize: 12 }} axisLine={false} tickLine={false} label={{ value: '% moving', angle: -90, fill: tc('#6b7280'), fontSize: 12 }} />
              <YAxis yAxisId="spd" orientation="right" tick={{ fill: tc('#9ca3af'), fontSize: 12 }} axisLine={false} tickLine={false} label={{ value: 'km/h', angle: 90, fill: tc('#6b7280'), fontSize: 12 }} />
              <Tooltip contentStyle={TOOLTIP_STYLE} />
              <Legend wrapperStyle={{ fontSize: 12 }} />
              <Bar yAxisId="pct" dataKey="moving_pct" name="% pings moving" fill={tc(tc('#3b82f6'))} radius={[3, 3, 0, 0]} />
              <Line yAxisId="spd" type="monotone" dataKey="avg_speed" name="Avg speed" stroke="#22c55e" strokeWidth={2} dot={false} />
            </ComposedChart>
          </ResponsiveContainer>
          <p className="text-center text-xs text-gray-500">Activity by hour of day — when this truck actually rolls</p>
        </div>
        <HeatmapGrid
          data={(a.driving.heatmap as any[]).map((c: any) => ({ ...c, value: c.activity }))}
          valueKey="value" label="Day × hour activity heatmap (ping density)" />
      </Section>

      {/* 6. Waypoint Journey */}
      <Section icon={Route} title="Waypoint Journey"
        subtitle="Consecutive GPS pings consolidated into a corridor visit timeline, with state-border crossings">
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-5">
          <Stat label="Waypoints Touched" value={a.waypoints.kpis.waypoints_touched} />
          <Stat label="Visits" value={a.waypoints.kpis.visits} />
          <Stat label="States" value={(a.waypoints.kpis.states_crossed as string[]).join(' → ')} accent="text-blue-400" />
          <Stat label="Border Crossings" value={a.waypoints.kpis.border_crossings} />
        </div>
        {a.waypoints.state_crossings.length > 0 && (
          <div className="flex flex-wrap gap-2 mb-5">
            {(a.waypoints.state_crossings as any[]).map((c: any, i: number) => (
              <span key={i} className="text-xs bg-blue-900/30 text-blue-300 border border-blue-800/50 rounded-lg px-3 py-1.5">
                {c.from_state} → {c.to_state} · {shortTime(c.ts)} · km {c.cum_km}{c.near ? ` · near ${c.near}` : ''}
              </span>
            ))}
          </div>
        )}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-5">
          <div>
            <BarChart
              data={(a.waypoints.top_dwell as any[]).map((d: any) => ({ ...d, name: d.waypoint.length > 18 ? d.waypoint.slice(0, 18) + '…' : d.waypoint, stopped_h: +(d.stopped_min / 60).toFixed(1) }))}
              xKey="name" series={[{ key: 'stopped_h', color: tc('#f59e0b'), label: 'Stopped hours' }]} height={240} />
            <p className="text-center text-xs text-gray-500">Where the time was lost — top dwell locations</p>
          </div>
          <div>
            <AreaChart
              data={(a.waypoints.visits as any[]).map((v: any, i: number) => ({ idx: i + 1, cum_km: v.cum_km }))}
              xKey="idx" series={[{ key: 'cum_km', color: tc('#3b82f6'), label: 'Cumulative km' }]} height={240} />
            <p className="text-center text-xs text-gray-500">Distance build-up across waypoint visits</p>
          </div>
        </div>
        <h3 className="text-sm font-semibold text-gray-300 mb-2">Visit timeline</h3>
        <div className="max-h-96 overflow-y-auto rounded-lg border border-gray-800">
          <DataTable columns={[
            { key: 'waypoint', label: 'Waypoint', render: (r: any) => <span className="text-gray-200 text-xs">{r.waypoint}{r.state ? ` (${r.state})` : ''}</span> },
            { key: 'arrive', label: 'Arrive', render: (r: any) => <span className="text-xs">{shortTime(r.arrive)}</span> },
            { key: 'dwell_min', label: 'Dwell', render: (r: any) => formatDuration(r.dwell_min) },
            { key: 'stopped_min', label: 'Stopped', render: (r: any) => <span className={r.stopped_min > 30 ? 'text-amber-400' : ''}>{formatDuration(r.stopped_min)}</span> },
            { key: 'km_covered', label: 'Km Here', render: (r: any) => r.km_covered },
            { key: 'cum_km', label: 'Cum. Km', render: (r: any) => <span className="text-blue-400">{r.cum_km}</span> },
            { key: 'avg_speed', label: 'Avg Speed', render: (r: any) => r.avg_speed ? `${r.avg_speed} km/h` : '-' },
          ]} data={a.waypoints.visits} />
        </div>
      </Section>

      {/* 7. Route Progress & Track */}
      <Section icon={Satellite} title="Route Progress & Track"
        subtitle="Cumulative distance over time, half-hour momentum, and the GPS trace">
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-5">
          <div>
            <AreaChart
              data={(a.progress.series as any[]).map((s: any) => ({ ...s, t: shortTime(s.t) }))}
              xKey="t" series={[{ key: 'km', color: tc('#3b82f6'), label: 'Cumulative km' }]} height={240} />
            <p className="text-center text-xs text-gray-500">Journey progress — flat stretches are standstills</p>
          </div>
          <div>
            <BarChart
              data={(a.progress.windows as any[]).map((w: any) => ({ ...w, label: w.window.split(' ').slice(-1)[0] }))}
              xKey="label" series={[{ key: 'km', color: '#22c55e', label: 'km / 30 min' }]} height={240} />
            <p className="text-center text-xs text-gray-500">Momentum — distance covered per 30-minute window</p>
          </div>
        </div>
        {gps && gps.points.length > 1 && (
          <LeafletTrackMap
            points={gps.points.map(p => ({ lat: p.d_lat, lng: p.d_long, moving: p.is_moving === 1 }))}
            hotZones={(a.stops.events as any[]).map((e: any) => ({
              lat: e.lat, lng: e.lng, minutes: e.minutes, label: e.near, reason: e.reason,
            }))}
            height={460}
          />
        )}
      </Section>

      {/* 8. Cost (rates are UI-configurable, stored in DB) */}
      <Section icon={IndianRupee} title="Estimated Journey Cost"
        subtitle={`Fuel ₹${a.cost.params.fuel_price_per_liter}/L at ${a.cost.params.fuel_efficiency_kmpl} km/L · driver ₹${a.cost.params.driver_wage_per_hour}/h · idling ${a.cost.params.idle_fuel_consumption_lph} L/h`}>
        <div className="flex justify-end mb-4">
          <CostConfigPanel params={a.cost.params} onSaved={refetch} />
        </div>
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          <ResponsiveContainer width="100%" height={220}>
            <PieChart>
              <Pie data={costDonut} dataKey="value" nameKey="name" innerRadius={55} outerRadius={85} paddingAngle={2}>
                {costDonut.map(d => <Cell key={d.name} fill={d.color} />)}
              </Pie>
              <Tooltip contentStyle={TOOLTIP_STYLE} formatter={(v: any) => `₹${formatNumber(v)}`} />
              <Legend wrapperStyle={{ fontSize: 12 }} />
            </PieChart>
          </ResponsiveContainer>
          <div className="lg:col-span-2 grid grid-cols-2 md:grid-cols-3 gap-3 content-start">
            <Stat label="Total Cost" value={`₹${formatNumber(a.cost.total_cost_inr)}`} accent="text-gray-100" />
            <Stat label="Cost / km" value={`₹${a.cost.cost_per_km}`} />
            <Stat label="Fuel Consumed" value={`${a.cost.fuel_liters} L`} accent="text-blue-400" />
            <Stat label="Fuel Cost" value={`₹${formatNumber(a.cost.fuel_cost_inr)}`} />
            <Stat label="Driver Cost" value={`₹${formatNumber(a.cost.driver_cost_inr)}`} accent="text-purple-400" />
            <Stat label="Idle Fuel Waste" value={`₹${formatNumber(a.cost.idle_waste_inr)}`} sub="engine-on standstill estimate" accent="text-amber-400" />
          </div>
        </div>
      </Section>

      <div className="flex items-center gap-2 text-xs text-gray-600 mb-2">
        <Clock className="w-3.5 h-3.5" /> Generated {formatDateTime(a.generated_at)} from {formatNumber(k.pings)} GPS pings ·
        <AlertTriangle className="w-3.5 h-3.5" /> movement times use gap-attribution capped at 15 min
      </div>
    </PageContainer>
  );
}
