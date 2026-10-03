import { useState } from 'react';
import {
  ScatterChart, Scatter, XAxis, YAxis, ZAxis, CartesianGrid, Tooltip,
  ReferenceLine, ResponsiveContainer, BarChart, Bar, ErrorBar, Cell,
} from 'recharts';
import { Table2, BarChart3, ScatterChart as ScatterIcon, X, ExternalLink } from 'lucide-react';
import { Link } from 'react-router-dom';
import Spinner from '../ui/Spinner';
import DataTable from '../ui/DataTable';
import { useApi } from '../../hooks/useApi';
import { getGpsDimensionDetail } from '../../services/geofence';
import type { TripClass } from '../../services/geofence';
import { tc } from '../../../../core/theme';

/**
 * GPS quality as an analysis rather than a wall of percentages.
 *
 * The table this replaces sorted carriers worst-first on a raw rate, which put
 * Ritco Logistics (20% of **5** trips) above Hind Transport (32.6% of **95**).
 * Ritco's true rate is somewhere between 4% and 62%; Hind's is pinned to a few
 * points. Ranking them by the same number sends someone to open a conversation
 * with the wrong carrier, so every view here carries the sample size and the
 * 95% Wilson interval, and the ranking is by the interval's **upper** bound —
 * "even at its most optimistic, this carrier is only this healthy". That is the
 * claim that survives a small sample: it puts Hind Transport (95 trips, best
 * case 42.6%) above Ritco (5 trips, best case 62.4%), which is the right way
 * round, because Ritco might genuinely be fine and Hind cannot be.
 *
 * Three views over the same numbers, because they answer different questions:
 *   • Evidence — is this carrier's rate real, or is it three trips?
 *   • Ranked   — who is worst once uncertainty is accounted for?
 *   • Mix      — *which* failure is theirs? Silent, died-at-origin, late and
 *                gappy have different owners and different fixes.
 *
 * Every mark opens the trips behind it, with the four raw inputs each verdict
 * was computed from. No number here is unfalsifiable.
 */

// Failure modes are a severity scale, and these five steps were checked with
// the palette validator against the dark chart surface rather than picked by
// eye: adjacent pairs (the only ones that touch in a stacked bar) clear the
// normal-vision floor and the lightness band. The amber/red pair sits in the
// CVD 6-8 band, which is legal only with secondary encoding — hence the 2px
// segment gaps, the legend, the direct labels and the table view below.
const MODE = [
  { key: 'ok', label: 'OK', color: '#059669' },
  { key: 'gappy', label: 'Gappy', color: tc('#2563eb') },
  { key: 'late_start', label: 'Late start', color: '#db2777' },
  { key: 'died_at_origin', label: 'Died at origin', color: '#c2830c' },
  { key: 'silent', label: 'Silent', color: '#dc2626' },
] as const;

const MODE_COLOR: Record<string, string> = Object.fromEntries(MODE.map(m => [m.key, m.color]));

const AXIS = tc('#6b7280');
const GRID = tc('#1f2937');
const MARK = '#38bdf8';

type Dim = 'transporter' | 'region';

/** One trip's evidence row, as returned by /geofence/gps/detail. */
interface TripEvidence {
  trip_no: number;
  verdict: string;
  ping_count: number | null;
  first_ping_lag_min: number | null;
  uptime_pct: number | null;
  geofence_status: string | null;
}

interface Row {
  transporter?: string; region?: string;
  trips: number;
  gps_ok_pct: number | null;
  gps_ok_ci_low: number | null;
  gps_ok_ci_high: number | null;
  gps_ok_ci_width: number | null;
  silent_trips: number; died_at_origin: number;
  late_start: number; gappy: number;
  gps_on_time_pct: number | null;
  median_ping_count: number;
}

const pct = (v: number | null | undefined) => (v == null ? '—' : `${v.toFixed(1)}%`);

function name(r: Row, dim: Dim) { return (dim === 'transporter' ? r.transporter : r.region) ?? '—'; }
function short(s: string, n = 22) { return s.length > n ? `${s.slice(0, n - 1)}…` : s; }

/** Everything a reader needs to judge one row, in one tooltip. */
function MarkTooltip({ active, payload, dim }: {
  active?: boolean; payload?: { payload: Row }[]; dim: Dim;
}) {
  if (!active || !payload?.length) return null;
  const r: Row = payload[0].payload;
  const okN = Math.round(((r.gps_ok_pct ?? 0) / 100) * r.trips);
  return (
    <div className="bg-gray-950 border border-gray-700 rounded-lg px-3 py-2 text-xs shadow-xl max-w-xs">
      <p className="text-white font-medium mb-1">{name(r, dim)}</p>
      <p className="text-gray-300">
        GPS OK <span className="font-semibold">{pct(r.gps_ok_pct)}</span>{' '}
        <span className="text-gray-500">({okN} of {r.trips} trips)</span>
      </p>
      <p className="text-gray-400">
        95% interval {pct(r.gps_ok_ci_low)} – {pct(r.gps_ok_ci_high)}
        {r.gps_ok_ci_width != null && r.gps_ok_ci_width > 30 && (
          <span className="text-amber-400"> · too few trips to rank</span>
        )}
      </p>
      <div className="mt-1.5 pt-1.5 border-t border-gray-800 text-gray-400 space-y-0.5">
        {MODE.filter(m => m.key !== 'ok').map(m => {
          const field = m.key === 'silent' ? 'silent_trips' : m.key;
          const v = (r as unknown as Record<string, number>)[field] ?? 0;
          return v > 0 ? (
            <p key={m.key}>
              <span className="inline-block w-2 h-2 rounded-sm mr-1.5" style={{ background: m.color }} />
              {m.label} <span className="text-gray-300">{v}</span>
            </p>
          ) : null;
        })}
      </div>
      <p className="text-gray-600 mt-1.5">click to see the trips</p>
    </div>
  );
}

function Legend() {
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-gray-400">
      {MODE.map(m => (
        <span key={m.key} className="flex items-center gap-1.5">
          <span className="w-2.5 h-2.5 rounded-sm" style={{ background: m.color }} />
          {m.label}
        </span>
      ))}
    </div>
  );
}

/** The trips behind one row, with the inputs each verdict came from. */
function DetailPanel({ dim, value, tripClass, onClose }: {
  dim: Dim; value: string; tripClass: TripClass; onClose: () => void;
}) {
  const { data, loading, error } = useApi(
    () => getGpsDimensionDetail(dim, value, tripClass), [dim, value, tripClass]);

  return (
    <div className="fixed inset-0 z-[2000]">
      <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" onClick={onClose} />
      <div className="absolute right-0 top-0 h-full w-full lg:w-3/5 max-w-4xl bg-gray-900
                      border-l border-gray-800 shadow-2xl flex flex-col">
        <div className="flex items-start justify-between gap-3 px-5 py-4 border-b border-gray-800 shrink-0">
          <div className="min-w-0">
            <h2 className="text-base font-semibold text-white truncate">{value}</h2>
            {data && (
              <p className="text-xs text-gray-500 mt-0.5">
                {data.trips} trips · GPS OK {pct(data.gps_ok_pct)}{' '}
                <span className="text-gray-600">
                  (95% interval {pct(data.gps_ok_ci_low)} – {pct(data.gps_ok_ci_high)})
                </span>
              </p>
            )}
          </div>
          <button onClick={onClose} className="text-gray-500 hover:text-gray-200 shrink-0">
            <X className="w-5 h-5" />
          </button>
        </div>

        <div className="overflow-y-auto p-5">
          {loading && <Spinner />}
          {error && <p className="text-sm text-red-400">{error}</p>}
          {data && (
            <>
              <DataTable
                columns={[
                  { key: 'trip', label: 'Trip' },
                  { key: 'verdict', label: 'Verdict' },
                  { key: 'pings', label: 'Pings' },
                  { key: 'lag', label: 'First ping vs start' },
                  { key: 'uptime', label: 'Uptime' },
                  { key: 'status', label: 'Geofence status' },
                ]}
                data={(data.trips_detail || []).map((t: TripEvidence) => ({
                  trip: (
                    <Link to={`/trips/${t.trip_no}`}
                      className="text-blue-400 hover:text-blue-300 inline-flex items-center gap-1">
                      {t.trip_no}<ExternalLink className="w-3 h-3" />
                    </Link>
                  ),
                  verdict: (
                    <span className="inline-flex items-center gap-1.5 text-xs">
                      <span className="w-2 h-2 rounded-sm"
                        style={{ background: MODE_COLOR[t.verdict] ?? AXIS }} />
                      {t.verdict}
                    </span>
                  ),
                  pings: <span className="text-gray-300">{t.ping_count ?? 0}</span>,
                  lag: t.first_ping_lag_min == null
                    ? <span className="text-gray-600">—</span>
                    : <span className={t.first_ping_lag_min > 30 ? 'text-pink-400' : 'text-gray-400'}>
                        {t.first_ping_lag_min > 0 ? '+' : ''}{t.first_ping_lag_min} min
                      </span>,
                  uptime: t.uptime_pct == null
                    ? <span className="text-gray-600">not reported</span>
                    : <span className={t.uptime_pct < 80 ? 'text-blue-400' : 'text-gray-400'}>
                        {t.uptime_pct}%
                      </span>,
                  status: <span className="text-xs text-gray-500">{t.geofence_status ?? '—'}</span>,
                }))}
                emptyMessage="No trips."
              />
              <div className="mt-4 rounded-lg border border-gray-800 bg-gray-950/40 p-3 space-y-1.5">
                <p className="text-xs uppercase tracking-wide text-gray-500">How to read these</p>
                {Object.entries(data.field_notes || {}).map(([k, v]) => (
                  <p key={k} className="text-xs text-gray-500">
                    <span className="text-gray-400 font-mono">{k}</span> — {String(v)}
                  </p>
                ))}
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

export default function GpsQualityAnalytics({ rows, loading, dim, tripClass, note }: {
  rows: Row[] | null; loading: boolean; dim: Dim; tripClass: TripClass; note?: string;
}) {
  const [view, setView] = useState<'evidence' | 'ranked' | 'mix' | 'table'>('evidence');
  const [drill, setDrill] = useState<string | null>(null);

  if (loading) return <Spinner />;
  const data = rows ?? [];
  if (!data.length) return <p className="text-sm text-gray-500">Nothing has enough trips to score.</p>;

  const totalTrips = data.reduce((s, r) => s + r.trips, 0);
  const fleetOk = data.reduce((s, r) => s + ((r.gps_ok_pct ?? 0) / 100) * r.trips, 0) / totalTrips * 100;

  // Ranked by the interval's UPPER bound, not the point estimate.
  //
  // "Even at its most optimistic, this carrier is still only X% healthy" is the
  // claim that survives a small sample. On this corpus it moves Hind Transport
  // (95 trips, 32.6%, best case 42.6%) to the top and drops Ritco (5 trips, 20%,
  // best case 62.4%) to sixth — which is the right way round to act on, because
  // Ritco might genuinely be fine and Hind cannot be.
  const ranked = [...data]
    .sort((a, b) => (a.gps_ok_ci_high ?? 100) - (b.gps_ok_ci_high ?? 100))
    .slice(0, 15)
    .map(r => ({
      ...r,
      label: short(name(r, dim)),
      // ErrorBar wants offsets from the value, not absolute bounds.
      ciErr: [(r.gps_ok_pct ?? 0) - (r.gps_ok_ci_low ?? 0),
              (r.gps_ok_ci_high ?? 0) - (r.gps_ok_pct ?? 0)] as [number, number],
    }));

  const mix = [...data]
    .sort((a, b) => (a.gps_ok_pct ?? 100) - (b.gps_ok_pct ?? 100))
    .slice(0, 15)
    .map(r => {
      const t = r.trips || 1;
      const okN = r.trips - r.silent_trips - r.died_at_origin - r.late_start - r.gappy;
      return {
        label: short(name(r, dim)),
        full: name(r, dim),
        trips: r.trips,
        ok: (okN / t) * 100,
        gappy: (r.gappy / t) * 100,
        late_start: (r.late_start / t) * 100,
        died_at_origin: (r.died_at_origin / t) * 100,
        silent: (r.silent_trips / t) * 100,
      };
    });

  const tripCounts = data.map(r => r.trips);
  const tripsMin = Math.max(1, Math.min(...tripCounts) * 0.85);
  const tripsMax = Math.max(...tripCounts) * 1.15;
  const xTicks = [5, 10, 20, 50, 100, 200, 500].filter(t => t >= tripsMin && t <= tripsMax);

  const VIEWS = [
    { id: 'evidence', label: 'Evidence', icon: ScatterIcon },
    { id: 'ranked', label: 'Ranked', icon: BarChart3 },
    { id: 'mix', label: 'Failure mix', icon: BarChart3 },
    { id: 'table', label: 'Table', icon: Table2 },
  ] as const;

  return (
    <>
      <div className="flex items-center justify-between flex-wrap gap-3 mb-4">
        <div className="flex gap-1 bg-gray-950 border border-gray-800 rounded-lg p-1">
          {VIEWS.map(v => (
            <button key={v.id} onClick={() => setView(v.id)}
              className={`flex items-center gap-1.5 px-2.5 py-1 rounded-md text-xs font-medium transition-colors ${
                view === v.id ? 'bg-blue-600/15 text-blue-400' : 'text-gray-400 hover:text-gray-200'
              }`}>
              <v.icon className="w-3.5 h-3.5" />{v.label}
            </button>
          ))}
        </div>
        <span className="text-xs text-gray-600">
          fleet average {fleetOk.toFixed(1)}% over {totalTrips.toLocaleString()} trips
        </span>
      </div>

      {view === 'evidence' && (
        <>
          <p className="text-xs text-gray-500 mb-3 max-w-4xl">
            Each point is one {dim}. Left of the chart means few trips, so a low rate there may be
            noise; the fleet average is the dashed line. The narrow band of points at the right is
            where the rate is actually pinned down.
          </p>
          <ResponsiveContainer width="100%" height={340}>
            <ScatterChart margin={{ top: 8, right: 16, bottom: 28, left: 4 }}>
              <CartesianGrid stroke={GRID} strokeDasharray="3 3" />
              {/* A log scale needs a concrete domain and its own ticks: recharts
                  collapses `domain={['auto','auto']}` on a log axis and draws
                  nothing. The range here is 5 to ~180 trips, which is exactly
                  the spread the chart exists to show. */}
              <XAxis type="number" dataKey="trips" name="Trips" scale="log"
                domain={[tripsMin, tripsMax]} ticks={xTicks} allowDataOverflow={false}
                stroke={AXIS} tick={{ fill: AXIS, fontSize: 12 }}
                label={{ value: 'Trips scored (log scale)', position: 'insideBottom', offset: -16,
                  fill: AXIS, fontSize: 12 }} />
              <YAxis type="number" dataKey="gps_ok_pct" name="GPS OK %" domain={[0, 100]}
                stroke={AXIS} tick={{ fill: AXIS, fontSize: 12 }} unit="%" />
              {/* ZAxis range is AREA, not radius: [40,400] gave 3.6-11px dots,
                  under the 8px floor and invisible once the page scales down.
                  [200,1600] is ~8-23px, which reads as a bubble chart should. */}
              <ZAxis type="number" dataKey="trips" range={[200, 1600]} />
              <ReferenceLine y={fleetOk} stroke={AXIS} strokeDasharray="4 4"
                label={{ value: `fleet ${fleetOk.toFixed(0)}%`, fill: AXIS, fontSize: 12,
                  position: 'insideTopRight' }} />
              <Tooltip content={<MarkTooltip dim={dim} />} cursor={{ strokeDasharray: '3 3' }} />
              <Scatter data={data} fill={MARK} fillOpacity={0.55}
                isAnimationActive={false} stroke="#0b0f19" strokeWidth={2} style={{ cursor: 'pointer' }}
                onClick={(p: unknown) => setDrill(name(p as Row, dim))} />
            </ScatterChart>
          </ResponsiveContainer>
        </>
      )}

      {view === 'ranked' && (
        <>
          <p className="text-xs text-gray-500 mb-3 max-w-4xl">
            Ranked by the <b>best case</b> — the top of the 95% interval — not the raw rate:
            “even at its most optimistic, this carrier is only this healthy”. That is the claim
            that survives a small sample, and it is why a carrier with 95 trips at 32.6% outranks
            one with 5 trips at 20%. The whisker is the interval; a long whisker means we do not
            know yet.
          </p>
          <ResponsiveContainer width="100%" height={Math.max(320, ranked.length * 30)}>
            <BarChart data={ranked} layout="vertical" margin={{ top: 4, right: 40, bottom: 20, left: 8 }}>
              <CartesianGrid stroke={GRID} strokeDasharray="3 3" horizontal={false} />
              <XAxis type="number" domain={[0, 100]} unit="%" stroke={AXIS}
                tick={{ fill: AXIS, fontSize: 12 }}
                label={{ value: 'GPS OK %', position: 'insideBottom', offset: -10,
                  fill: AXIS, fontSize: 12 }} />
              <YAxis type="category" dataKey="label" width={150} stroke={AXIS}
                tick={{ fill: tc('#9ca3af'), fontSize: 12 }} />
              <ReferenceLine x={fleetOk} stroke={AXIS} strokeDasharray="4 4" />
              <Tooltip content={<MarkTooltip dim={dim} />} cursor={{ fill: `${tc('#ffffff')}08` }} />
              <Bar dataKey="gps_ok_pct" radius={[0, 4, 4, 0]} barSize={13}
                isAnimationActive={false} style={{ cursor: 'pointer' }}
                onClick={(p: unknown) => setDrill(name(p as Row, dim))}>
                {ranked.map(r => (
                  <Cell key={r.label}
                    fill={(r.gps_ok_ci_width ?? 0) > 30 ? '#475569' : MARK} />
                ))}
                <ErrorBar dataKey="ciErr" width={4} strokeWidth={1.5} stroke="#94a3b8" />
              </Bar>
            </BarChart>
          </ResponsiveContainer>
          <p className="text-xs text-gray-600 mt-2">
            Grey bars have an interval wider than 30 points — too few trips to act on yet.
          </p>
        </>
      )}

      {view === 'mix' && (
        <>
          <p className="text-xs text-gray-500 mb-3 max-w-4xl">
            The same trips, split by <b>which</b> failure. This is the actionable view: “silent”
            is a device that never reported, “died at origin” is a tracker that quit inside the
            works, “late start” missed the loading window. Different owners, different fixes.
          </p>
          <ResponsiveContainer width="100%" height={Math.max(320, mix.length * 30)}>
            {/* No stackOffset="expand": the five values are already percentages of
                the row's trips and sum to 100, so re-normalising them produced
                zero-width segments. A plain stack on a 0-100 axis is both
                simpler and what the numbers already mean. */}
            <BarChart data={mix} layout="vertical"
              margin={{ top: 4, right: 24, bottom: 20, left: 8 }}>
              <CartesianGrid stroke={GRID} strokeDasharray="3 3" horizontal={false} />
              <XAxis type="number" domain={[0, 100]} unit="%" stroke={AXIS}
                tick={{ fill: AXIS, fontSize: 12 }} />
              <YAxis type="category" dataKey="label" width={150} stroke={AXIS}
                tick={{ fill: tc('#9ca3af'), fontSize: 12 }} />
              <Tooltip cursor={{ fill: `${tc('#ffffff')}08` }}
                contentStyle={{ background: tc('#030712'), border: `1px solid ${tc('#374151')}`,
                  borderRadius: 8, fontSize: 12 }}
                formatter={(v?: number | string, n?: string) => [`${Number(v ?? 0).toFixed(1)}%`, n ?? '']} />
              {MODE.map(m => (
                <Bar key={m.key} dataKey={m.key} stackId="a" name={m.label}
                  fill={m.color} barSize={13} isAnimationActive={false}
                  /* 2px surface gap so adjacent segments never abut */
                  stroke={tc(tc('#111827'))} strokeWidth={2}
                  style={{ cursor: 'pointer' }}
                  onClick={(p: unknown) => setDrill((p as { full?: string })?.full ?? null)} />
              ))}
            </BarChart>
          </ResponsiveContainer>
          <div className="mt-2"><Legend /></div>
        </>
      )}

      {view === 'table' && (
        <DataTable
          columns={[
            { key: 'name', label: dim === 'transporter' ? 'Transporter' : 'Region', className: 'text-white' },
            { key: 'trips', label: 'Trips' },
            { key: 'ok', label: 'GPS OK' },
            { key: 'ci', label: '95% interval' },
            { key: 'silent', label: 'Silent' },
            { key: 'died', label: 'Died at origin' },
            { key: 'late', label: 'Late start' },
            { key: 'gappy', label: 'Gappy' },
            { key: 'onTime', label: 'GPS on time' },
          ]}
          data={data.map(r => ({
            name: (
              <button onClick={() => setDrill(name(r, dim))}
                className="text-left hover:text-blue-400 transition-colors">{name(r, dim)}</button>
            ),
            trips: r.trips,
            ok: <span className="text-gray-200">{pct(r.gps_ok_pct)}</span>,
            ci: (
              <span className={(r.gps_ok_ci_width ?? 0) > 30 ? 'text-amber-400 text-xs' : 'text-gray-500 text-xs'}>
                {pct(r.gps_ok_ci_low)} – {pct(r.gps_ok_ci_high)}
              </span>
            ),
            silent: r.silent_trips || <span className="text-gray-600">0</span>,
            died: r.died_at_origin || <span className="text-gray-600">0</span>,
            late: r.late_start || <span className="text-gray-600">0</span>,
            gappy: r.gappy || <span className="text-gray-600">0</span>,
            onTime: <span className="text-gray-400">{pct(r.gps_on_time_pct)}</span>,
          }))}
          emptyMessage="Nothing to score."
        />
      )}

      {note && <p className="text-xs text-gray-500 mt-3 max-w-4xl">{note}</p>}

      {drill && (
        <DetailPanel dim={dim} value={drill} tripClass={tripClass} onClose={() => setDrill(null)} />
      )}
    </>
  );
}
