import { useState } from 'react';
import {
  Factory, Warehouse, Scale, PackageOpen, DoorOpen, ParkingCircle,
  Sparkles, AlertTriangle, MapPin, Clock, TrendingUp, ChevronDown, ChevronUp,
} from 'lucide-react';
import { useApi } from '../../hooks/useApi';
import { getTTAPlantDelay } from '../../services/tta';
import { formatDuration } from '../../lib/formatters';
import Spinner from '../ui/Spinner';
import JourneyBreakdown from './JourneyBreakdown';
import type { Phases } from './JourneyBreakdown';
import { tc } from '../../../../core/theme';

const CATEGORY_STYLE: Record<string, { color: string; icon: any }> = {
  'Yard / parking wait': { color: tc('#f59e0b'), icon: ParkingCircle },
  'Gate queue': { color: tc('#06b6d4'), icon: DoorOpen },
  'Weighbridge': { color: tc('#a855f7'), icon: Scale },
  'Loading / plant floor': { color: tc('#10b981'), icon: PackageOpen },
  'Inside plant': { color: tc('#3b82f6'), icon: Factory },
  'Standing / halt': { color: tc('#6b7280'), icon: Clock },
};

const SEVERITY_STYLE: Record<string, { label: string; bg: string; text: string; ring: string }> = {
  critical: { label: 'Critical', bg: 'bg-red-500/15', text: 'text-red-400', ring: 'ring-red-500/30' },
  high: { label: 'High', bg: 'bg-orange-500/15', text: 'text-orange-400', ring: 'ring-orange-500/30' },
  moderate: { label: 'Moderate', bg: 'bg-amber-500/15', text: 'text-amber-400', ring: 'ring-amber-500/30' },
  low: { label: 'Low', bg: 'bg-emerald-500/15', text: 'text-emerald-400', ring: 'ring-emerald-500/30' },
};

function catStyle(cat: string) {
  return CATEGORY_STYLE[cat] || CATEGORY_STYLE['Standing / halt'];
}

function shortTime(iso: string | null | undefined): string {
  if (!iso) return '-';
  return new Date(iso).toLocaleString('en-IN', {
    day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit',
  });
}

function Mini({ label, value, sub, accent = 'text-gray-100' }:
  { label: string; value: React.ReactNode; sub?: string; accent?: string }) {
  return (
    <div className="bg-gray-800/50 rounded-lg p-3">
      <p className="text-xs text-gray-500 mb-1">{label}</p>
      <p className={`text-lg font-bold leading-tight ${accent}`}>{value}</p>
      {sub && <p className="text-xs text-gray-500 mt-0.5">{sub}</p>}
    </div>
  );
}

export default function PlantDelaySection({ tripNo }: { tripNo: number | string }) {
  const { data, loading, error } = useApi(() => getTTAPlantDelay(tripNo), [tripNo]);
  const [showAll, setShowAll] = useState(false);
  const [expanded, setExpanded] = useState(false);

  const Shell = ({ children }: { children: React.ReactNode }) => (
    <div className="bg-gray-900 rounded-xl border border-gray-800 p-5 mb-6">
      <div className="mb-4">
        <h2 className="text-lg font-semibold text-white flex items-center gap-2">
          <Factory className="w-5 h-5 text-amber-400" /> In-Plant Delay Analysis
        </h2>
        <p className="text-xs text-gray-500 mt-1 max-w-4xl">
          Measured against the plant geofence — a 10 km circle on the published plant coordinate,
          not on wherever this truck happened to start. The ping trail below explains where the
          time went inside that window.
        </p>
      </div>
      {children}
    </div>
  );

  if (loading) return <Shell><Spinner /></Shell>;
  if (error || !data) return null;                       // silently skip if unavailable
  // A dead tracker does not erase the stamps, so the declared window is still
  // reportable; only the ping-trail reconstruction below it is lost.
  if (!data.has_gps) return (
    <Shell>
      {data.phases && <JourneyBreakdown phases={data.phases as Phases} />}
      <p className="text-gray-500 text-sm">
        No GPS pings stored for this trip, so where the time went inside the plant cannot be
        reconstructed. The windows above come from the trip stamps and still hold.
      </p>
    </Shell>
  );

  const k = data.kpis;
  const dr = data.delay_reason || {};
  const ins = data.insights;
  const sev = SEVERITY_STYLE[ins?.severity] || SEVERITY_STYLE.moderate;
  const bStyle = catStyle(dr.primary || 'Standing / halt');
  const BIcon = bStyle.icon;

  const inPlant = Number(k.in_plant_dwell_min || 0);
  const yard = Number(k.yard_dwell_min || 0);
  const splitTotal = inPlant + yard || 1;
  const inPlantPct = Math.round((inPlant / splitTotal) * 100);
  const excess = Number(k.excess_over_benchmark_min || 0);

  const stations: any[] = data.stations || [];
  const visible = showAll ? stations : stations.slice(0, 6);

  return (
    <Shell>
      {/* The two windows the business defined -- the headline numbers. */}
      {data.phases && <JourneyBreakdown phases={data.phases as Phases} />}

      {/* Verdict banner */}
      <div className={`rounded-lg ring-1 ${sev.ring} ${sev.bg} p-4 mb-5`}>
        <div className="flex items-start justify-between gap-3 flex-wrap">
          <div className="flex items-start gap-3">
            <div className="mt-0.5 w-9 h-9 rounded-lg flex items-center justify-center shrink-0"
              style={{ background: `${bStyle.color}22` }}>
              <BIcon className="w-5 h-5" style={{ color: bStyle.color }} />
            </div>
            <div>
              <p className="text-xs uppercase tracking-wide text-gray-400 mb-0.5">Primary delay reason</p>
              <p className="text-base font-semibold text-white">
                {dr.primary}{dr.location ? <> · <span className="text-gray-300">{dr.location}</span></> : null}
              </p>
              {dr.dwell_min != null && (
                <p className="text-xs text-gray-400 mt-0.5">
                  {formatDuration(dr.dwell_min)} present
                  {dr.dist_from_origin_km != null && <> · {dr.dist_from_origin_km} km from plant anchor</>}
                  {dr.share_of_idle_pct != null && <> · {dr.share_of_idle_pct}% of non-driving time</>}
                </p>
              )}
            </div>
          </div>
          <span className={`px-2.5 py-1 rounded-full text-xs font-semibold ${sev.bg} ${sev.text} ring-1 ${sev.ring}`}>
            {sev.label} severity
          </span>
        </div>
      </div>

      {/* Collapsible: headline (verdict) stays above; the full breakdown is a dropdown */}
      <button onClick={() => setExpanded(v => !v)}
        className="w-full flex items-center justify-center gap-1.5 py-2 rounded-lg text-xs font-medium text-gray-400 hover:text-gray-200 border border-gray-800 bg-gray-800/40 mb-5">
        {expanded
          ? <><ChevronUp className="w-4 h-4" /> Hide breakdown</>
          : <><ChevronDown className="w-4 h-4" /> Show where the time went — GPS window, station leaderboard &amp; AI insight</>}
      </button>

      {expanded && (
      <>
      {/* KPI strip */}
      <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3 mb-5">
        <Mini label="Origin window" value={formatDuration(k.origin_window_min)}
          sub={`first ping → left the ${data.params?.radius_km ?? 10} km fence`} />
        <Mini label="Non-driving (idle)" value={formatDuration(k.idle_min)} accent="text-amber-400" sub={`${k.utilization_pct}% moving`} />
        <Mini label="Inside plant" value={formatDuration(inPlant)} accent="text-blue-400" sub="gate / weighbridge / dock" />
        <Mini label="At yards / outside" value={formatDuration(yard)} accent="text-orange-400" sub="parking away from gate" />
        <Mini label="vs benchmark" value={`${excess >= 0 ? '+' : ''}${formatDuration(Math.abs(excess))}`}
          accent={excess > 0 ? 'text-red-400' : 'text-emerald-400'} sub={`SLA ${formatDuration(k.benchmark_min)}`} />
        <Mini label="Provider plant vivo" value={data.declared?.plant_vivo_label || '-'} accent="text-gray-400"
          sub="for contrast only — not counted" />
      </div>

      {/* Inside-plant vs yard split bar */}
      <div className="mb-5">
        <div className="flex justify-between text-xs text-gray-400 mb-1.5">
          <span className="flex items-center gap-1.5"><Factory className="w-3 h-3 text-blue-400" /> Inside plant · {inPlantPct}%</span>
          <span className="flex items-center gap-1.5">Yards / outside · {100 - inPlantPct}% <Warehouse className="w-3 h-3 text-orange-400" /></span>
        </div>
        <div className="h-2.5 rounded-full overflow-hidden bg-gray-800 flex">
          <div className="bg-blue-500" style={{ width: `${inPlantPct}%` }} />
          <div className="bg-orange-500" style={{ width: `${100 - inPlantPct}%` }} />
        </div>
      </div>

      {/* Where the time went — station leaderboard */}
      <div className="mb-5">
        <h3 className="text-sm font-semibold text-gray-300 mb-2 flex items-center gap-1.5">
          <MapPin className="w-4 h-4 text-gray-500" /> Where the time went
        </h3>
        <div className="space-y-1.5">
          {visible.map((s, i) => {
            const cs = catStyle(s.category);
            const Icon = cs.icon;
            const barPct = Math.min(100, Math.round((s.dwell_min / (stations[0]?.dwell_min || 1)) * 100));
            return (
              <div key={i} className="bg-gray-800/40 rounded-lg px-3 py-2">
                <div className="flex items-center justify-between gap-2">
                  <div className="flex items-center gap-2 min-w-0">
                    <Icon className="w-4 h-4 shrink-0" style={{ color: cs.color }} />
                    <span className="text-sm text-gray-200 truncate">{s.waypoint}</span>
                    <span className="px-1.5 py-0.5 rounded text-xs font-medium shrink-0"
                      style={{ background: `${cs.color}22`, color: cs.color }}>{s.category}</span>
                  </div>
                  <div className="text-right shrink-0">
                    <span className="text-sm font-semibold text-gray-100">{formatDuration(s.dwell_min)}</span>
                    <span className="text-xs text-gray-500 ml-1.5">{s.dist_from_origin_km} km</span>
                  </div>
                </div>
                <div className="h-1 rounded-full bg-gray-800 mt-1.5 overflow-hidden">
                  <div className="h-full rounded-full" style={{ width: `${barPct}%`, background: cs.color }} />
                </div>
                <p className="text-xs text-gray-600 mt-1">
                  {shortTime(s.arrive)} → {shortTime(s.depart)} · {formatDuration(s.stopped_min)} confirmed stopped
                </p>
              </div>
            );
          })}
        </div>
        {stations.length > 6 && (
          <button onClick={() => setShowAll(v => !v)}
            className="mt-2.5 inline-flex items-center gap-1 text-xs text-blue-400 hover:text-blue-300 font-medium">
            {showAll ? <><ChevronUp className="w-3.5 h-3.5" /> Show less</>
              : <><ChevronDown className="w-3.5 h-3.5" /> See all {stations.length} stations</>}
          </button>
        )}
      </div>

      {/* AI insight */}
      {ins && (
        <div className="rounded-lg border border-gray-800 bg-gradient-to-br from-gray-800/40 to-gray-900 p-4">
          <div className="flex items-center justify-between mb-2 flex-wrap gap-2">
            <h3 className="text-sm font-semibold text-white flex items-center gap-1.5">
              <Sparkles className="w-4 h-4 text-violet-400" /> AI Insight
            </h3>
            <span className="text-xs text-gray-500">
              {ins.source === 'azure_openai'
                ? `${ins.model || 'gpt'} · confidence ${ins.confidence ?? '-'}`
                : 'rule-based (add Azure key for LLM)'}
            </span>
          </div>
          {ins.headline && <p className="text-sm text-gray-100 font-medium mb-2">{ins.headline}</p>}
          {ins.root_cause && (
            <p className="text-xs text-gray-400 mb-2 leading-relaxed">
              <span className="text-gray-500 font-medium">Root cause — </span>{ins.root_cause}
            </p>
          )}
          {ins.what_gps_shows && (
            <p className="text-xs text-gray-400 mb-3 leading-relaxed">
              <span className="text-gray-500 font-medium">What GPS shows — </span>{ins.what_gps_shows}
            </p>
          )}
          {Array.isArray(ins.recommendations) && ins.recommendations.length > 0 && (
            <div>
              <p className="text-xs uppercase tracking-wide text-gray-500 mb-1.5 flex items-center gap-1">
                <TrendingUp className="w-3.5 h-3.5" /> Recommendations
              </p>
              <ul className="space-y-1">
                {ins.recommendations.map((r: string, i: number) => (
                  <li key={i} className="text-xs text-gray-300 flex items-start gap-1.5">
                    <span className="text-violet-400 mt-0.5">›</span><span>{r}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
          {ins.llm_error && (
            <p className="text-xs text-amber-500/70 mt-2 flex items-center gap-1">
              <AlertTriangle className="w-3 h-3" /> LLM call failed — showing rule-based fallback.
            </p>
          )}
        </div>
      )}
      </>
      )}
    </Shell>
  );
}
