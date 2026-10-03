import { useState } from 'react';
import {
  ScatterChart, Scatter, XAxis, YAxis, ZAxis, CartesianGrid, Tooltip,
  ReferenceLine, ResponsiveContainer, BarChart, Bar, Cell,
} from 'recharts';
import {
  Table2, BarChart3, ScatterChart as ScatterIcon, X, ExternalLink, AlertTriangle,
} from 'lucide-react';
import { Link } from 'react-router-dom';
import Spinner from '../ui/Spinner';
import DataTable from '../ui/DataTable';
import { useApi } from '../../hooks/useApi';
import { getDestinationDetail } from '../../services/geofence';
import type { TripClass } from '../../services/geofence';
import { tc } from '../../../../core/theme';

/**
 * Where the GPS trail actually stops, per destination — as an analysis.
 *
 * The distinction this screen exists to protect is **conclusive vs not**. A
 * fence sourced `gazetteer` is a town centroid, chosen because the GPS-derived
 * position failed its sanity check; a 30 km gap against one of those means the
 * customer's gate is not precisely known, *not* that the truck fell short.
 * Presenting both in one ranked list would turn a data-quality gap into an
 * accusation about a carrier, so conclusive lanes are coloured and ranked
 * separately and the rest are explicitly greyed.
 *
 * The same sample-size discipline as the GPS scorecard applies: "6% arrived"
 * on six trips is not the same claim as on sixty, so the arrived rate carries
 * a 95% Wilson interval everywhere it appears.
 */

// Three states, not two. `gap_is_conclusive` only says the MEASUREMENT can be
// trusted -- it is true for a 0.5 km gap as much as a 1,400 km one. Colouring
// on it alone painted 54 of 56 lanes red and read as "54 lanes have a
// shortfall", when most of those lanes arrive perfectly well.
const SHORTFALL = '#dc2626';    // trustworthy measurement AND genuinely short
const ARRIVED = '#059669';      // trail reaches the fence
const UNCERTAIN = '#64748b';    // town-centroid fence: we don't know the gate
const AXIS = tc('#6b7280');
const GRID = tc('#1f2937');

/** Beyond this the trail is short enough to be worth investigating. */
const SHORTFALL_KM = 25;

// Only these two, and neither is a "series" colour: this is a status
// distinction (act / cannot yet act), so it stays out of the categorical ramp.
const FENCE_SOURCE_NOTE: Record<string, string> = {
  gazetteer: 'town centroid — the GPS-derived position failed its sanity check',
  anchor: 'derived from the GPS trail and corroborated',
  anchor_unverified: 'derived from the GPS trail, nothing to check it against',
  plant: 'published plant coordinate',
  client: 'supplied by the consignor',
};

interface GapRow {
  destination: string;
  trips: number;
  median_end_gap_km: number;
  p90_end_gap_km: number;
  arrived_pct: number | null;
  arrived_trips: number;
  arrived_ci_low: number | null;
  arrived_ci_high: number | null;
  arrived_ci_width: number | null;
  stopped_over_50km_short: number;
  fence_source: string;
  gap_is_conclusive: boolean;
}

interface TripRow {
  trip_no: number;
  transporter: string | null;
  close_reason: string | null;
  end_gap_km: number | null;
  arrived: boolean | null;
  distance_km: number | null;
  pct_of_lane_median: number | null;
  upstream_geo_id: number | null;
  superseded: boolean;
  ping_count: number | null;
}

type State = 'shortfall' | 'arrived' | 'uncertain';

function state(r: GapRow): State {
  if (!r.gap_is_conclusive) return 'uncertain';
  return r.median_end_gap_km > SHORTFALL_KM ? 'shortfall' : 'arrived';
}

const STATE_COLOR: Record<State, string> = {
  shortfall: SHORTFALL, arrived: ARRIVED, uncertain: UNCERTAIN,
};

const STATE_LABEL: Record<State, string> = {
  shortfall: 'Stops short',
  arrived: 'Reaches the fence',
  uncertain: 'Gate not precisely known',
};

const pct = (v: number | null | undefined) => (v == null ? '—' : `${v.toFixed(1)}%`);
const km = (v: number | null | undefined) =>
  v == null ? '—' : v >= 10 ? `${Math.round(v).toLocaleString()} km` : `${v.toFixed(1)} km`;
const short = (s: string, n = 20) => (s.length > n ? `${s.slice(0, n - 1)}…` : s);

function GapTooltip({ active, payload }: { active?: boolean; payload?: { payload: GapRow }[] }) {
  if (!active || !payload?.length) return null;
  const r = payload[0].payload;
  return (
    <div className="bg-gray-950 border border-gray-700 rounded-lg px-3 py-2 text-xs shadow-xl max-w-xs">
      <p className="text-white font-medium mb-1">{r.destination}</p>
      <p className="text-gray-300">
        Trail ends <span className="font-semibold">{km(r.median_end_gap_km)}</span> from the fence
        <span className="text-gray-500"> (median of {r.trips} trips)</span>
      </p>
      <p className="text-gray-400">
        Arrived {pct(r.arrived_pct)}{' '}
        <span className="text-gray-600">
          [{pct(r.arrived_ci_low)} – {pct(r.arrived_ci_high)}]
        </span>
      </p>
      <p className="mt-1.5 pt-1.5 border-t border-gray-800" style={{ color: STATE_COLOR[state(r)] }}>
        {{
          shortfall: 'The trail genuinely stops short of the fence.',
          arrived: 'The trail reaches the fence — nothing wrong here.',
          uncertain: 'This fence is a town centroid, so the gap may just mean we do not know where the gate is.',
        }[state(r)]}
      </p>
      <p className="text-gray-600 mt-1">click to see the trips</p>
    </div>
  );
}

function DetailPanel({ destination, tripClass, onClose }: {
  destination: string; tripClass: TripClass; onClose: () => void;
}) {
  const { data, loading, error } = useApi(
    () => getDestinationDetail(destination, tripClass), [destination, tripClass]);
  const [hideSuperseded, setHideSuperseded] = useState(true);

  const all: TripRow[] = data?.trips_detail ?? [];
  const rows = hideSuperseded ? all.filter(t => !t.superseded) : all;

  return (
    <div className="fixed inset-0 z-[2000]">
      <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" onClick={onClose} />
      <div className="absolute right-0 top-0 h-full w-full lg:w-3/5 max-w-4xl bg-gray-900
                      border-l border-gray-800 shadow-2xl flex flex-col">
        <div className="flex items-start justify-between gap-3 px-5 py-4 border-b border-gray-800 shrink-0">
          <div className="min-w-0">
            <h2 className="text-base font-semibold text-white truncate">{destination}</h2>
            {data && (
              <p className="text-xs text-gray-500 mt-0.5">
                {data.trips} trips · {data.arrived_trips} arrived · median gap{' '}
                {km(data.median_end_gap_km)}
                {data.fence && (
                  <span className="text-gray-600"> · fence “{data.fence.source}”</span>
                )}
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
              {data.fence?.source === 'gazetteer' && (
                <div className="mb-4 rounded-lg border border-amber-900/50 bg-amber-950/20 p-3 flex items-start gap-2">
                  <AlertTriangle className="w-4 h-4 text-amber-400 mt-0.5 shrink-0" />
                  <p className="text-xs text-amber-200/80">
                    This fence is a town centroid, not the customer's gate. Read the gaps below
                    as “we do not know exactly where this customer is”, not as “the truck fell
                    short” — unless the gap is hundreds of kilometres.
                  </p>
                </div>
              )}

              {data.superseded_trips > 0 && (
                <label className="flex items-center gap-2 mb-3 text-xs text-gray-400 cursor-pointer">
                  <input type="checkbox" checked={hideSuperseded}
                    onChange={e => setHideSuperseded(e.target.checked)}
                    className="accent-blue-500" />
                  Hide {data.superseded_trips} superseded trips
                  <span className="text-gray-600">
                    (closed “NEW TRIP FOUND…” — replaced, not delivered, so their last ping says
                    nothing about the destination)
                  </span>
                </label>
              )}

              <DataTable
                columns={[
                  { key: 'trip', label: 'Trip' },
                  { key: 'gap', label: 'Trail ended' },
                  { key: 'ran', label: 'Distance run' },
                  { key: 'share', label: 'vs lane median' },
                  { key: 'fence', label: 'eTrans fence' },
                  { key: 'closed', label: 'Closed as' },
                ]}
                data={rows.map(t => ({
                  trip: (
                    <Link to={`/trips/${t.trip_no}`}
                      className="text-blue-400 hover:text-blue-300 inline-flex items-center gap-1">
                      {t.trip_no}<ExternalLink className="w-3 h-3" />
                    </Link>
                  ),
                  gap: t.end_gap_km == null
                    ? <span className="text-gray-600">no pings</span>
                    : <span className={t.arrived ? 'text-emerald-400' : 'text-gray-300'}>
                        {km(t.end_gap_km)} out{t.arrived ? ' · arrived' : ''}
                      </span>,
                  ran: <span className="text-gray-400">{km(t.distance_km)}</span>,
                  share: t.pct_of_lane_median == null
                    ? <span className="text-gray-600">—</span>
                    : <span className={t.pct_of_lane_median >= 90 ? 'text-amber-400' : 'text-gray-400'}>
                        {t.pct_of_lane_median}%
                      </span>,
                  fence: (t.upstream_geo_id || 0) > 0
                    ? <span className="text-emerald-400 text-xs">id {t.upstream_geo_id}</span>
                    : <span className="text-red-400 text-xs">none</span>,
                  closed: <span className="text-xs text-gray-500">{t.close_reason ?? '—'}</span>,
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

export default function DestinationCoverageAnalytics({ rows, loading, tripClass }: {
  rows: GapRow[] | null; loading: boolean; tripClass: TripClass;
}) {
  const [view, setView] = useState<'evidence' | 'ranked' | 'table'>('evidence');
  const [drill, setDrill] = useState<string | null>(null);

  if (loading) return <Spinner />;
  const data = rows ?? [];
  if (!data.length) return <p className="text-sm text-gray-500">No lane has enough trips to score.</p>;

  // A log y-axis cannot plot a zero gap, and "arrived exactly on the fence" is
  // a real and common outcome, so the floor is a tenth of a km rather than a
  // dropped point.
  const plot = data.map(r => ({ ...r, gapPlot: Math.max(r.median_end_gap_km, 0.1) }));
  const tripsMin = Math.max(1, Math.min(...data.map(r => r.trips)) * 0.85);
  const tripsMax = Math.max(...data.map(r => r.trips)) * 1.15;
  const xTicks = [5, 10, 20, 50, 100, 200, 500].filter(t => t >= tripsMin && t <= tripsMax);

  const ranked = [...data]
    .filter(r => r.median_end_gap_km > 5)
    .sort((a, b) => b.median_end_gap_km - a.median_end_gap_km)
    .slice(0, 15)
    .map(r => ({ ...r, label: short(r.destination) }));

  const counts: Record<State, number> = { shortfall: 0, arrived: 0, uncertain: 0 };
  data.forEach(r => { counts[state(r)] += 1; });

  const VIEWS = [
    { id: 'evidence', label: 'Evidence', icon: ScatterIcon },
    { id: 'ranked', label: 'Ranked', icon: BarChart3 },
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
        <div className="flex items-center gap-4 flex-wrap text-xs text-gray-400">
          {(['shortfall', 'arrived', 'uncertain'] as State[]).map(st => (
            <span key={st} className="flex items-center gap-1.5">
              <span className="w-2.5 h-2.5 rounded-full" style={{ background: STATE_COLOR[st] }} />
              {STATE_LABEL[st]} ({counts[st]})
            </span>
          ))}
        </div>
      </div>

      {view === 'evidence' && (
        <>
          <p className="text-xs text-gray-500 mb-3 max-w-4xl">
            How far the trail stops from the destination, against how many trips say so. Up and to
            the right is a real, well-evidenced shortfall. Grey points are lanes whose fence is a
            town centroid — the gap there may just mean the customer's exact gate is unknown.
          </p>
          <ResponsiveContainer width="100%" height={340}>
            <ScatterChart margin={{ top: 8, right: 16, bottom: 28, left: 8 }}>
              <CartesianGrid stroke={GRID} strokeDasharray="3 3" />
              <XAxis type="number" dataKey="trips" scale="log" domain={[tripsMin, tripsMax]}
                ticks={xTicks} stroke={AXIS} tick={{ fill: AXIS, fontSize: 12 }}
                label={{ value: 'Trips on the lane (log)', position: 'insideBottom', offset: -16,
                  fill: AXIS, fontSize: 12 }} />
              <YAxis type="number" dataKey="gapPlot" scale="log" domain={[0.1, 'auto']}
                ticks={[0.1, 1, 10, 100, 1000]} stroke={AXIS} tick={{ fill: AXIS, fontSize: 12 }}
                tickFormatter={(v: number) => (v < 1 ? '0' : `${v}`)}
                label={{ value: 'Median gap to fence, km (log)', angle: -90,
                  position: 'insideLeft', fill: AXIS, fontSize: 12 }} />
              <ZAxis type="number" dataKey="trips" range={[200, 1400]} />
              <ReferenceLine y={10} stroke={AXIS} strokeDasharray="4 4"
                label={{ value: 'arrived (10 km)', fill: AXIS, fontSize: 12,
                  position: 'insideBottomRight' }} />
              <Tooltip content={<GapTooltip />} cursor={{ strokeDasharray: '3 3' }} />
              <Scatter data={plot} isAnimationActive={false} stroke="#0b0f19" strokeWidth={2}
                style={{ cursor: 'pointer' }}
                onClick={(p: unknown) => setDrill((p as GapRow).destination)}>
                {plot.map(r => (
                  <Cell key={r.destination}
                    fill={STATE_COLOR[state(r)]} fillOpacity={0.6} />
                ))}
              </Scatter>
            </ScatterChart>
          </ResponsiveContainer>
        </>
      )}

      {view === 'ranked' && (
        <>
          <p className="text-xs text-gray-500 mb-3 max-w-4xl">
            Median distance from the trail's last ping to the destination fence. Grey bars are not
            evidence of a shortfall — their fence is a town centroid, so the gap is uncertainty
            about the customer's location rather than a claim about the truck.
          </p>
          <ResponsiveContainer width="100%" height={Math.max(320, ranked.length * 30)}>
            <BarChart data={ranked} layout="vertical" margin={{ top: 4, right: 44, bottom: 20, left: 8 }}>
              <CartesianGrid stroke={GRID} strokeDasharray="3 3" horizontal={false} />
              <XAxis type="number" scale="log" domain={[1, 'auto']}
                ticks={[1, 10, 100, 1000]} stroke={AXIS} tick={{ fill: AXIS, fontSize: 12 }}
                label={{ value: 'Median gap to fence, km (log)', position: 'insideBottom',
                  offset: -10, fill: AXIS, fontSize: 12 }} />
              <YAxis type="category" dataKey="label" width={150} stroke={AXIS}
                tick={{ fill: tc('#9ca3af'), fontSize: 12 }} />
              <Tooltip content={<GapTooltip />} cursor={{ fill: `${tc('#ffffff')}08` }} />
              <Bar dataKey="median_end_gap_km" radius={[0, 4, 4, 0]} barSize={13}
                isAnimationActive={false} style={{ cursor: 'pointer' }}
                onClick={(p: unknown) => setDrill((p as GapRow).destination)}>
                {ranked.map(r => (
                  <Cell key={r.destination} fill={STATE_COLOR[state(r)]} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </>
      )}

      {view === 'table' && (
        <DataTable
          columns={[
            { key: 'destination', label: 'Destination', className: 'text-white' },
            { key: 'trips', label: 'Trips' },
            { key: 'gap', label: 'Median gap' },
            { key: 'p90', label: 'p90 gap' },
            { key: 'arrived', label: 'Arrived' },
            { key: 'ci', label: '95% interval' },
            { key: 'fence', label: 'Fence source' },
            { key: 'verdict', label: 'Reading' },
          ]}
          data={data.map(r => ({
            destination: (
              <button onClick={() => setDrill(r.destination)}
                className="text-left hover:text-blue-400 transition-colors">{r.destination}</button>
            ),
            trips: r.trips,
            gap: (
              <span className={state(r) === 'shortfall' ? 'text-red-400 font-medium' : 'text-gray-300'}>
                {km(r.median_end_gap_km)}
              </span>
            ),
            p90: <span className="text-gray-500">{km(r.p90_end_gap_km)}</span>,
            arrived: <span className="text-gray-200">{pct(r.arrived_pct)}</span>,
            ci: (
              <span className={(r.arrived_ci_width ?? 0) > 30 ? 'text-amber-400 text-xs' : 'text-gray-500 text-xs'}>
                {pct(r.arrived_ci_low)} – {pct(r.arrived_ci_high)}
              </span>
            ),
            fence: (
              <span className="text-xs text-gray-400" title={FENCE_SOURCE_NOTE[r.fence_source]}>
                {r.fence_source}
              </span>
            ),
            verdict: (
              <span className="text-xs" style={{ color: STATE_COLOR[state(r)] }}>
                {STATE_LABEL[state(r)]}
              </span>
            ),
          }))}
          emptyMessage="Nothing to score."
        />
      )}

      {drill && (
        <DetailPanel destination={drill} tripClass={tripClass} onClose={() => setDrill(null)} />
      )}
    </>
  );
}
