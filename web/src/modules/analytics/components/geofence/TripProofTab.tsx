import { useState } from 'react';
import { Clock, Timer, TrendingUp, Search, MapPin } from 'lucide-react';
import KPICard from '../ui/KPICard';
import { ProofGrid } from '../../../../core/proof/ProofPanel';
import Spinner from '../ui/Spinner';
import GeofenceMap from './GeofenceMap';
import type { FenceShape } from './GeofenceMap';
import { useApi } from '../../hooks/useApi';
import { getDemoTrips, getTripTrack } from '../../services/geofence';
import type { TripTrack } from '../../services/geofence';

/**
 * One trip, on a map, with the hidden detention drawn rather than asserted.
 *
 * This tab exists because the aggregate does not persuade. "Median hidden tail
 * 7.9 h" invites "your data is wrong"; a single truck whose trail stays red
 * inside the works for two days after its gate-out stamp does not. Everything
 * on this screen is one trip, so a client can check it against their own
 * records line by line.
 */

const STATUS_COPY: Record<string, { label: string; tone: string; blurb: string }> = {
  ok: {
    label: 'Confirmed exit',
    tone: 'text-emerald-400 border-emerald-900/60 bg-emerald-950/20',
    blurb: 'The trail crossed out of the origin fence and stayed out. The exit stamp is trustworthy.',
  },
  intra_fence: {
    label: 'Delivery inside the origin fence',
    tone: 'text-blue-400 border-blue-900/60 bg-blue-950/20',
    blurb: 'The destination sits inside the origin fence, so there is no exit to find. This is correct, not a failure.',
  },
  gps_died_at_origin: {
    label: 'Tracker died inside the works',
    tone: 'text-amber-400 border-amber-900/60 bg-amber-950/20',
    blurb: 'The trail stops inside the origin fence on a long-haul run. The truck went; the tracker did not. This trip looks healthy in a ping count.',
  },
  never_exited: {
    label: 'No confirmed exit',
    tone: 'text-amber-400 border-amber-900/60 bg-amber-950/20',
    blurb: 'No sustained exit was seen, and the destination has no fence to judge the trip by.',
  },
  no_gps: {
    label: 'No GPS at all',
    tone: 'text-red-400 border-red-900/60 bg-red-950/20',
    blurb: 'This trip has no pings. Nothing can be measured from the trail.',
  },
};

const h = (v: number | null | undefined) => (v == null ? '—' : `${v.toFixed(1)} h`);
const clock = (s: string | null) => (s ? s.replace('T', ' ').slice(0, 16) : '—');

/** Declared vs hidden, as one bar. The proportion is the point. */
function DetentionBar({ declared, hidden }: { declared: number | null; hidden: number | null }) {
  const d = declared ?? 0;
  const hd = hidden ?? 0;
  const total = d + hd;
  if (total <= 0) return null;
  const dPct = (d / total) * 100;

  return (
    <div>
      <div className="flex h-9 rounded-lg overflow-hidden border border-gray-800">
        <div
          className="bg-blue-600/70 flex items-center justify-center text-xs font-medium text-blue-50"
          style={{ width: `${dPct}%` }}
          title={`Declared works detention: ${h(declared)}`}
        >
          {dPct > 14 && `${d.toFixed(1)} h declared`}
        </div>
        <div
          className="bg-red-600/70 flex items-center justify-center text-xs font-medium text-red-50"
          style={{ width: `${100 - dPct}%` }}
          title={`Hidden tail after gate-out: ${h(hidden)}`}
        >
          {100 - dPct > 14 && `${hd.toFixed(1)} h hidden`}
        </div>
      </div>
      <div className="flex justify-between text-xs text-gray-500 mt-1.5">
        <span>plant entry</span>
        <span>gate-out stamp</span>
        <span>actually cleared the works</span>
      </div>
    </div>
  );
}

function TripDetail({ track }: { track: TripTrack }) {
  const { trip, detention, fences, route, sampling } = track;
  const status = STATUS_COPY[trip.geofence_out_status ?? ''] ?? {
    label: trip.geofence_out_status ?? 'unknown',
    tone: 'text-gray-400 border-gray-800 bg-gray-900',
    blurb: '',
  };

  const shapes: FenceShape[] = [];
  if (fences.origin) shapes.push(fences.origin);
  if (fences.destination) shapes.push(fences.destination);

  // The exit stamp is placed on the last ping observed inside, never
  // interpolated, so it is a real point on the trail — find it to mark it.
  const exitPoint = (() => {
    if (!trip.dt_geofence_out) return null;
    const stamp = trip.dt_geofence_out.replace('T', ' ').slice(0, 19);
    const hit = route.find(p => String(p.t).replace('T', ' ').slice(0, 19) === stamp);
    if (hit) return { lat: hit.lat, lon: hit.lon, t: stamp };
    const lastInside = [...route].reverse().find(p => p.in_origin);
    return lastInside ? { lat: lastInside.lat, lon: lastInside.lon, t: stamp } : null;
  })();

  return (
    <>
      <ProofGrid className="grid grid-cols-1 md:grid-cols-4 gap-4 mb-6">
        <KPICard label="Declared by the TMS" color="blue" icon={Clock}
          value={h(detention.declared_h)}
          proof={{ dataset: 'tripproof.hold', params: { trip_no: trip.trip_no, metric: 'declared' }, value: detention.declared_h }} />
        <KPICard label="Hidden after gate-out" color="red" icon={Timer}
          value={h(detention.hidden_tail_h)}
          proof={{ dataset: 'tripproof.hold', params: { trip_no: trip.trip_no, metric: 'hidden' }, value: detention.hidden_tail_h }} />
        <KPICard label="True total hold" color="purple" icon={Clock}
          value={h(detention.true_total_h)}
          proof={{ dataset: 'tripproof.hold', params: { trip_no: trip.trip_no, metric: 'total' }, value: detention.true_total_h }} />
        <KPICard label="Understated by" color="amber" icon={TrendingUp}
          value={detention.understated_by_pct == null ? '—' : `${detention.understated_by_pct}%`}
          proof={{ dataset: 'tripproof.hold', params: { trip_no: trip.trip_no, metric: 'understated' }, value: detention.understated_by_pct }} />
      </ProofGrid>

      <div className="bg-gray-900 rounded-xl border border-gray-800 p-5 mb-6">
        <div className="flex items-start justify-between flex-wrap gap-3 mb-4">
          <div>
            <h2 className="text-lg font-semibold text-white">
              Trip {trip.trip_no} · {trip.origin} → {trip.destination}
            </h2>
            <p className="text-xs text-gray-500 mt-1">
              {trip.transporter || 'carrier not recorded'}
              {trip.asset_id ? ` · vehicle ${trip.asset_id}` : ''}
              {trip.close_reason ? ` · closed "${trip.close_reason}"` : ''}
            </p>
          </div>
          <span className={`text-xs px-2.5 py-1 rounded-md border ${status.tone}`}>
            {status.label}
          </span>
        </div>

        {status.blurb && <p className="text-xs text-gray-400 mb-4 max-w-4xl">{status.blurb}</p>}

        <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-5 text-sm">
          {[
            ['Plant entry (booking)', clock(trip.dt_booking)],
            ['Gate-out stamp', clock(trip.dt_trip_start)],
            ['Cleared the works belt', clock(trip.dt_geofence_out)],
            ['Arrived at destination', clock(trip.dt_trip_ata)],
          ].map(([label, value]) => (
            <div key={label}>
              <p className="text-xs text-gray-500 uppercase tracking-wide">{label}</p>
              <p className="text-gray-200 font-medium mt-0.5">{value}</p>
            </div>
          ))}
        </div>

        <DetentionBar declared={detention.declared_h} hidden={detention.hidden_tail_h} />

        {trip.geofence_out_gap_min != null && (
          <p className="text-xs text-gray-500 mt-3">
            The exit stamp sits on the last ping seen inside the fence; the next ping came{' '}
            {trip.geofence_out_gap_min} min later, so that gap is the uncertainty on the
            time — it is never interpolated.
          </p>
        )}
      </div>

      <div className="bg-gray-900 rounded-xl border border-gray-800 p-5 mb-6">
        <div className="mb-4">
          <h2 className="text-lg font-semibold text-white">The trail against the fence</h2>
          <p className="text-xs text-gray-500 mt-1 max-w-4xl">
            Red is every ping still inside the origin geofence. Where red continues past the
            amber exit marker, the truck was still in the works after the TMS had already
            stamped it out of the gate — that stretch is the detention nobody was billing for.
          </p>
        </div>
        <GeofenceMap route={route} fences={shapes} exitPoint={exitPoint} height={540} />
        <p className="text-xs text-gray-600 mt-2">
          {sampling.pings_total.toLocaleString()} pings on this trip;{' '}
          {sampling.points_returned.toLocaleString()} drawn.{' '}
          {sampling.in_origin_kept_whole.toLocaleString()} inside the fence are kept whole —
          only the highway leg is thinned{sampling.route_stride > 1
            ? ` (every ${sampling.route_stride}${sampling.route_stride === 2 ? 'nd' : 'th'} point)`
            : ''}.
        </p>
      </div>
    </>
  );
}

export default function TripProofTab() {
  const [tripNo, setTripNo] = useState<number | null>(null);
  const [search, setSearch] = useState('');

  const { data: demos, loading: demosLoading } = useApi(() => getDemoTrips(12), []);
  const active = tripNo ?? demos?.[0]?.trip_no ?? null;

  const { data: track, loading, error } = useApi(
    // Nothing chosen yet: resolve empty rather than firing a request, so
    // `track` stays null and the placeholder renders.
    () => (active
      ? getTripTrack(active)
      : Promise.resolve({ data: null as unknown as TripTrack })),
    [active],
  );

  return (
    <>
      <div className="bg-gray-900 rounded-xl border border-gray-800 p-5 mb-6">
        <div className="flex items-start justify-between flex-wrap gap-3 mb-4">
          <div>
            <h2 className="text-lg font-semibold text-white">Pick a trip</h2>
            <p className="text-xs text-gray-500 mt-1 max-w-3xl">
              Two thirds of the corpus delivers inside the Jamshedpur belt, where there is
              correctly nothing to see. These are chosen instead: the longest confirmed hidden
              tails, and trips whose tracker died inside the works.
            </p>
          </div>
          <form
            className="flex items-center gap-2"
            onSubmit={e => {
              e.preventDefault();
              const n = parseInt(search.trim(), 10);
              if (!Number.isNaN(n)) setTripNo(n);
            }}
          >
            <div className="relative">
              <Search className="w-3.5 h-3.5 text-gray-500 absolute left-2.5 top-1/2 -translate-y-1/2" />
              <input
                value={search}
                onChange={e => setSearch(e.target.value)}
                placeholder="Trip number"
                className="bg-gray-950 border border-gray-800 rounded-md pl-8 pr-3 py-1.5 text-sm
                           text-gray-200 placeholder-gray-600 w-40 focus:outline-none
                           focus:border-blue-700"
              />
            </div>
            <button
              type="submit"
              className="px-3 py-1.5 rounded-md text-sm font-medium bg-blue-600/15 text-blue-400
                         hover:bg-blue-600/25 transition-colors"
            >
              Open
            </button>
          </form>
        </div>

        {demosLoading ? (
          <Spinner />
        ) : (
          <div className="flex gap-2 overflow-x-auto pb-1">
            {(demos ?? []).map(d => (
              <button
                key={d.trip_no}
                onClick={() => setTripNo(d.trip_no)}
                className={`shrink-0 text-left rounded-lg border px-3 py-2 transition-colors ${
                  active === d.trip_no
                    ? 'border-blue-700 bg-blue-950/30'
                    : 'border-gray-800 bg-gray-950 hover:border-gray-700'
                }`}
              >
                <p className="text-xs font-medium text-gray-200 whitespace-nowrap">
                  {d.origin} → {d.destination}
                </p>
                <p className="text-xs text-gray-500 whitespace-nowrap mt-0.5">
                  {d.hidden_tail_h != null
                    ? `${d.hidden_tail_h.toFixed(1)} h hidden`
                    : 'tracker died at origin'}
                  {' · '}#{d.trip_no}
                </p>
              </button>
            ))}
          </div>
        )}
      </div>

      {error && (
        <div className="rounded-lg border border-red-900/60 bg-red-950/20 p-3 mb-6">
          <p className="text-sm text-red-400">{error}</p>
        </div>
      )}
      {loading && <Spinner />}
      {!loading && !track && !error && (
        <p className="text-gray-500 text-sm flex items-center gap-2">
          <MapPin className="w-4 h-4" /> Choose a trip above.
        </p>
      )}
      {!loading && track && <TripDetail track={track} />}
    </>
  );
}
