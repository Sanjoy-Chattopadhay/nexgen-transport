import { Clock, Timer, Gauge, TrendingUp, Ban, MapPinned } from 'lucide-react';
import { formatDuration } from '../../lib/formatters';

/**
 * The origin journey, split into the two windows the business defined.
 *
 *   dt_booking ───────► dt_trip_start ───────► dt_geofence_out
 *    plant entry          gate-out stamp       cleared the 10 km plant fence
 *
 *   (A) Plant / Works detention -- the figure the TMS declares.
 *   (B) Origin geofence         -- still inside the fence after the gate-out
 *                                  stamp. Nobody was billing for this.
 *   (A+B) True hold at origin.
 *
 * These are the headline numbers, so they sit at the top as KPIs. Everything
 * reconstructed from the ping trail -- the station leaderboard, the delay
 * classification, the AI insight -- is detail that explains them, and lives
 * below in the collapsible section.
 *
 * "Transporter Park IN - Transporter Park OUT" is excluded by the business as
 * unusable. It is named on screen rather than simply omitted, so a reader can
 * see it was a decision rather than an oversight.
 */

export interface Phase {
  label: string;
  definition: string;
  from_ts: string | null;
  to_ts: string | null;
  minutes: number | null;
  note?: string;
  status?: string | null;
  uncertainty_min?: number | null;
  understated_by_pct?: number | null;
}

export interface Phases {
  works_detention: Phase;
  geofence_exit: Phase;
  origin_total: Phase;
  excluded: { transporter_park: string };
}

const stamp = (s: string | null | undefined) =>
  s ? new Date(s).toLocaleString('en-IN', {
    day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit',
  }) : '—';

function Kpi({ label, value, sub, icon: Icon, tone, title }: {
  label: string; value: string; sub: string;
  icon: typeof Clock; tone: string; title?: string;
}) {
  return (
    <div className="bg-gray-950/60 rounded-xl border border-gray-800 p-4" title={title}>
      <div className="flex items-start justify-between gap-2">
        <p className="text-xs uppercase tracking-wide text-gray-500 leading-tight">{label}</p>
        <Icon className={`w-4 h-4 shrink-0 opacity-60 ${tone}`} />
      </div>
      <p className={`text-2xl font-bold mt-1.5 ${tone}`}>{value}</p>
      <p className="text-xs text-gray-500 mt-1 leading-snug">{sub}</p>
    </div>
  );
}

export default function JourneyBreakdown({ phases }: { phases: Phases }) {
  const a = phases.works_detention;
  const b = phases.geofence_exit;
  const total = phases.origin_total;

  const aMin = a.minutes ?? 0;
  const bMin = b.minutes ?? 0;
  const span = aMin + bMin;
  // A trip with no confirmed geofence exit has no (B) to draw. Showing a full
  // blue bar would read as "all accounted for", which is the opposite of true.
  const measurable = b.minutes != null && span > 0;
  const aPct = measurable ? (aMin / span) * 100 : 100;

  return (
    <div className="mb-5">
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 mb-4">
        <Kpi
          label="Plant / Works detention"
          value={formatDuration(a.minutes)}
          sub="Plant entry → gate-out. The figure the TMS declares."
          icon={Clock} tone="text-blue-400" title={a.definition}
        />
        <Kpi
          label={b.label}
          value={b.minutes == null ? 'not measurable' : formatDuration(b.minutes)}
          sub={b.minutes == null
            ? (b.status ? `No confirmed exit (${b.status}).` : 'No confirmed geofence exit.')
            : 'Gate-out → cleared the plant geofence.'}
          icon={Timer} tone={b.minutes == null ? 'text-gray-500' : 'text-red-400'}
          title={b.definition}
        />
        <Kpi
          label="True hold at origin"
          value={total.minutes == null ? '—' : formatDuration(total.minutes)}
          sub="Plant entry → cleared the geofence."
          icon={Gauge} tone="text-purple-400" title={total.definition}
        />
        <Kpi
          label="Declared figure understates by"
          value={total.understated_by_pct == null ? '—' : `${total.understated_by_pct}%`}
          sub="Share of the true hold the gate-out stamp misses."
          icon={TrendingUp} tone="text-amber-400"
        />
      </div>

      {/* The proportion is the argument, so draw it. */}
      <div className="rounded-xl border border-gray-800 bg-gray-950/40 p-4">
        <div className="flex items-center gap-1.5 mb-2.5">
          <MapPinned className="w-3.5 h-3.5 text-gray-500" />
          <h3 className="text-xs font-semibold text-gray-300">The origin journey</h3>
        </div>

        {measurable ? (
          <div className="flex h-9 rounded-lg overflow-hidden border border-gray-800">
            <div
              className="bg-blue-600/70 flex items-center justify-center text-xs font-medium text-blue-50 min-w-0"
              style={{ width: `${aPct}%` }}
              title={`${a.label}: ${formatDuration(a.minutes)}`}
            >
              {aPct > 18 && <span className="truncate px-2">{formatDuration(a.minutes)} works detention</span>}
            </div>
            <div
              className="bg-red-600/70 flex items-center justify-center text-xs font-medium text-red-50 min-w-0"
              style={{ width: `${100 - aPct}%` }}
              title={`${b.label}: ${formatDuration(b.minutes)}`}
            >
              {100 - aPct > 18 && <span className="truncate px-2">{formatDuration(b.minutes)} inside the fence</span>}
            </div>
          </div>
        ) : (
          // No confirmed geofence exit: the works detention is the ONLY measured
          // quantity, so it is drawn full width and the unmeasured tail is not
          // drawn at all.
          //
          // It used to be a second flex-1 segment beside a width:100% bar. The
          // two fought for the same row, which crushed the grey block into a
          // sliver at the right edge with its caption spilling over the bar --
          // and worse, it implied the unknown remainder had a size. It does not;
          // that is the whole point of it being unmeasurable.
          <>
            <div className="flex h-9 rounded-lg overflow-hidden border border-gray-800">
              <div
                className="w-full bg-blue-600/70 flex items-center justify-center text-xs font-medium text-blue-50 min-w-0"
                title={`${a.label}: ${formatDuration(a.minutes)}`}
              >
                <span className="truncate px-2">{formatDuration(a.minutes)} works detention</span>
              </div>
            </div>
            <p className="text-xs text-gray-500 mt-2 flex items-start gap-1.5">
              <Ban className="w-3 h-3 mt-0.5 shrink-0 text-gray-600" />
              <span>
                No confirmed geofence exit{b.status ? ` (${b.status})` : ''}, so the bar shows only
                the declared window. The true hold at origin is <strong className="text-gray-400">at
                least</strong> this and unknown beyond it — not equal to it.
              </span>
            </p>
          </>
        )}

        <div className="grid grid-cols-3 text-xs mt-2">
          <div>
            <p className="text-gray-500">Plant entry</p>
            <p className="text-gray-300 font-medium">{stamp(a.from_ts)}</p>
          </div>
          <div className="text-center">
            <p className="text-gray-500">Gate-out stamp</p>
            <p className="text-gray-300 font-medium">{stamp(a.to_ts)}</p>
          </div>
          <div className="text-right">
            <p className="text-gray-500">Cleared the geofence</p>
            <p className="text-gray-300 font-medium">{stamp(b.to_ts)}</p>
          </div>
        </div>

        {b.uncertainty_min != null && (
          <p className="text-xs text-gray-600 mt-2.5">
            The exit is stamped on the last ping seen inside the fence; the next ping came{' '}
            {b.uncertainty_min} min later, which is the uncertainty on that time. It is never
            interpolated.
          </p>
        )}

        <p className="text-xs text-gray-600 mt-2 flex items-start gap-1.5">
          <Ban className="w-3 h-3 mt-0.5 shrink-0 text-gray-700" />
          {phases.excluded.transporter_park}
        </p>
      </div>
    </div>
  );
}
