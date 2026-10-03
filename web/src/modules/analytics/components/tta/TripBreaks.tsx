import { useState } from 'react';
import {
  Coffee, UtensilsCrossed, Moon, CloudRain, PackageOpen, Warehouse,
  Timer, HelpCircle, MapPin, ChevronDown, ChevronUp, Pause,
} from 'lucide-react';
import { useApi } from '../../hooks/useApi';
import { getTTATripBreaks } from '../../services/tta';
import { formatDuration } from '../../lib/formatters';
import Spinner from '../ui/Spinner';
import { tc } from '../../../../core/theme';

/**
 * Where the hours went on this trip, instead of a dump of GPS pings.
 *
 * The table this replaced printed fifty rows of the same fact — the truck was
 * parked at HSM CANTEEN — one row per ping. This shows the halts themselves:
 * how many, how long, what kind, and, above all, how much of it nobody has an
 * account for.
 *
 * `unexplained_hours` is the number to look at. Loading, unloading, night rest
 * and meals are the cost of doing business; the residue is where a question
 * lives.
 */

const STYLE: Record<string, { color: string; icon: typeof Coffee }> = {
  'Loading / At plant': { color: tc('#10b981'), icon: PackageOpen },
  'Detention at origin': { color: tc('#ef4444'), icon: Timer },
  'Unloading / Detention': { color: tc('#a855f7'), icon: Warehouse },
  'Extended standstill': { color: '#f97316', icon: Pause },
  'Night rest': { color: '#6366f1', icon: Moon },
  'Weather halt': { color: '#38bdf8', icon: CloudRain },
  'Long halt': { color: '#f97316', icon: Pause },
  'Extended halt': { color: tc('#f59e0b'), icon: Pause },
  'Lunch break': { color: '#22c55e', icon: UtensilsCrossed },
  'Dinner break': { color: '#14b8a6', icon: UtensilsCrossed },
  'Tea / short break': { color: tc('#06b6d4'), icon: Coffee },
  Halt: { color: tc('#9ca3af'), icon: HelpCircle },
};

const styleFor = (r: string) => STYLE[r] ?? STYLE.Halt;

const clock = (s: string | null) =>
  s ? new Date(s).toLocaleString('en-IN', {
    day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit',
  }) : '—';

interface BreakRow {
  seq: number; start: string; end: string; minutes: number;
  reason: string; base_reason: string; rule: string;
  near: string | null; state: string | null;
  weather: { label: string; adverse: boolean; rain_mm: number | null;
             temp_c: number | null } | null;
}

interface Category {
  reason: string; count: number; total_min: number;
  longest_min: number; avg_min: number; share_pct: number;
}

function Kpi({ label, value, sub, tone = 'text-gray-100' }:
  { label: string; value: string; sub?: string; tone?: string }) {
  return (
    <div className="bg-gray-800/50 rounded-lg p-3">
      <p className="text-xs text-gray-500 mb-1">{label}</p>
      <p className={`text-lg font-bold leading-tight ${tone}`}>{value}</p>
      {sub && <p className="text-xs text-gray-500 mt-0.5">{sub}</p>}
    </div>
  );
}

function Shell({ children }: { children: React.ReactNode }) {
  return (
    <div className="bg-gray-900 rounded-xl border border-gray-800 p-5">
      <div className="mb-4">
        <h2 className="text-lg font-semibold text-white flex items-center gap-2">
          <Pause className="w-5 h-5 text-cyan-400" /> Breaks &amp; Halts
        </h2>
        <p className="text-xs text-gray-500 mt-1 max-w-4xl">
          Every standstill from trip start to close, named by trip phase first, then by
          duration and time of day, then by weather. Times are the first and last ping of
          each halt — never interpolated.
        </p>
      </div>
      {children}
    </div>
  );
}

export default function TripBreaks({ tripNo }: { tripNo: number | string }) {
  const { data, loading, error } = useApi(() => getTTATripBreaks(tripNo), [tripNo]);
  const [showAll, setShowAll] = useState(false);

  if (loading) return <Shell><Spinner /></Shell>;
  if (error || !data) return null;
  if (!data.has_gps) return (
    <Shell><p className="text-gray-500 text-sm">No GPS pings stored for this trip — halts can't be reconstructed.</p></Shell>
  );

  const k = data.kpis;
  const cats: Category[] = data.categories || [];
  const breaks: BreakRow[] = data.breaks || [];
  const shown = showAll ? breaks : breaks.slice(0, 12);
  const maxCat = Math.max(...cats.map(c => c.total_min), 1);

  return (
    <Shell>
      <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3 mb-5">
        <Kpi label="Breaks taken" value={String(k.total_breaks)} sub={`${k.distinct_places} distinct places`} />
        <Kpi label="Total halted" value={`${k.total_break_hours} h`} tone="text-amber-400" />
        <Kpi label="Longest single halt" value={formatDuration(k.longest_break_min)}
          tone="text-orange-400" sub={k.longest_break_where ?? undefined} />
        <Kpi label="Unexplained" value={`${k.unexplained_hours} h`}
          tone={k.unexplained_hours > 0 ? 'text-red-400' : 'text-emerald-400'}
          sub={`${k.unexplained_breaks} halts with no account`} />
        <Kpi label="Weather halts" value={`${k.weather_hours} h`} tone="text-sky-400"
          sub="adverse hour, no other reason" />
        <Kpi label="Rest &amp; meals" value={formatDuration(
          cats.filter(c => ['Night rest', 'Lunch break', 'Dinner break', 'Tea / short break']
            .includes(c.reason)).reduce((s, c) => s + c.total_min, 0))}
          tone="text-indigo-400" sub="night rest + meal breaks" />
      </div>

      {/* Where the hours went, by kind */}
      <div className="mb-5">
        <h3 className="text-sm font-semibold text-gray-300 mb-2">Break time by kind</h3>
        <div className="space-y-1.5">
          {cats.map(c => {
            const st = styleFor(c.reason);
            const Icon = st.icon;
            return (
              <div key={c.reason} className="bg-gray-800/40 rounded-lg px-3 py-2">
                <div className="flex items-center justify-between gap-2">
                  <div className="flex items-center gap-2 min-w-0">
                    <Icon className="w-4 h-4 shrink-0" style={{ color: st.color }} />
                    <span className="text-sm text-gray-200 truncate">{c.reason}</span>
                    <span className="text-xs text-gray-500 shrink-0">
                      ×{c.count} · avg {formatDuration(c.avg_min)}
                    </span>
                  </div>
                  <div className="text-right shrink-0">
                    <span className="text-sm font-semibold text-gray-100">{formatDuration(c.total_min)}</span>
                    <span className="text-xs text-gray-500 ml-1.5">{c.share_pct}%</span>
                  </div>
                </div>
                <div className="h-1 rounded-full bg-gray-800 mt-1.5 overflow-hidden">
                  <div className="h-full rounded-full"
                    style={{ width: `${(c.total_min / maxCat) * 100}%`, background: st.color }} />
                </div>
              </div>
            );
          })}
        </div>
      </div>

      {/* The register itself */}
      <div>
        <h3 className="text-sm font-semibold text-gray-300 mb-2 flex items-center gap-1.5">
          <MapPin className="w-4 h-4 text-gray-500" /> Every break, in order
        </h3>
        <div className="space-y-1.5">
          {shown.map(b => {
            const st = styleFor(b.reason);
            const Icon = st.icon;
            return (
              <div key={b.seq} className="bg-gray-800/40 rounded-lg px-3 py-2">
                <div className="flex items-start justify-between gap-2">
                  <div className="flex items-start gap-2 min-w-0">
                    <Icon className="w-4 h-4 mt-0.5 shrink-0" style={{ color: st.color }} />
                    <div className="min-w-0">
                      <div className="flex items-center gap-2 flex-wrap">
                        <span className="px-1.5 py-0.5 rounded text-xs font-medium shrink-0"
                          style={{ background: `${st.color}22`, color: st.color }}>{b.reason}</span>
                        <span className="text-sm text-gray-200 truncate">{b.near ?? 'unnamed location'}</span>
                        {b.weather?.adverse && (
                          <span className="text-xs text-sky-400 flex items-center gap-1 shrink-0">
                            <CloudRain className="w-3 h-3" />{b.weather.label}
                            {b.weather.rain_mm ? ` ${b.weather.rain_mm} mm` : ''}
                          </span>
                        )}
                      </div>
                      <p className="text-xs text-gray-600 mt-0.5">
                        {clock(b.start)} → {clock(b.end)} · {b.rule}
                        {b.reason !== b.base_reason && (
                          <span className="text-gray-700"> · re-labelled from “{b.base_reason}”</span>
                        )}
                      </p>
                    </div>
                  </div>
                  <span className="text-sm font-semibold text-gray-100 shrink-0">
                    {formatDuration(b.minutes)}
                  </span>
                </div>
              </div>
            );
          })}
        </div>
        {breaks.length > 12 && (
          <button onClick={() => setShowAll(v => !v)}
            className="mt-2.5 inline-flex items-center gap-1 text-xs text-blue-400 hover:text-blue-300 font-medium">
            {showAll ? <><ChevronUp className="w-3.5 h-3.5" /> Show less</>
              : <><ChevronDown className="w-3.5 h-3.5" /> See all {breaks.length} breaks</>}
          </button>
        )}
      </div>

      {data.weather?.error && (
        <p className="text-xs text-amber-500/70 mt-3">
          Weather lookup failed ({data.weather.error}) — halts are still named by phase,
          duration and time of day.
        </p>
      )}
      <p className="text-xs text-gray-600 mt-3">
        A halt is only called a weather halt when the hour was adverse <em>and</em> it had no
        explanation of its own — a lunch break in the rain stays a lunch break.
      </p>
    </Shell>
  );
}
