/**
 * A trip's number line in miniature, for tables: each segment's share of the
 * trip's span, in the phase colours. Read from geo_trip_phase.j_bar, so a
 * list of 25 trips costs no extra request.
 */
import { segmentColor, segmentOpacity, PHASE_LABEL, KIND_TEXT } from './phaseColors';
import { fmtDuration } from '../../lib/format';

type Piece = [string, string, number, number];

export function parseBar(bar: string | null | undefined): Piece[] {
  if (!bar) return [];
  try {
    const v = JSON.parse(bar);
    return Array.isArray(v) ? v : [];
  } catch {
    return [];
  }
}

export function PhaseBar({ trip, width = 140, height = 10 }: { trip: any; width?: number; height?: number }) {
  const pieces = parseBar(trip?.j_bar);
  if (!pieces.length) return <span className="text-gray-600 text-xs">—</span>;
  const span = Math.max(1, ...pieces.map(p => p[2] + p[3]));
  const title = [
    trip.i_loading_s != null ? `Loading ${fmtDuration(trip.i_loading_s)}` : null,
    trip.i_transit_moving_s != null ? `driving ${fmtDuration(trip.i_transit_moving_s)}` : null,
    trip.i_transit_stop_s ? `stopped ${fmtDuration(trip.i_transit_stop_s)}` : null,
    trip.i_transit_halt_s ? `halts ${fmtDuration(trip.i_transit_halt_s)}` : null,
    trip.i_transit_silent_s ? `GPS silent ${fmtDuration(trip.i_transit_silent_s)}` : null,
    trip.i_unloading_s != null ? `unloading ${fmtDuration(trip.i_unloading_s)}` : null,
  ].filter(Boolean).join(' · ') || (trip.s_shape === 'one_place' ? 'One place only' : 'No place seen');
  return (
    <svg width={width} height={height} className="block rounded-sm overflow-hidden" role="img" aria-label={title}>
      <title>{title}</title>
      <rect x={0} y={0} width={width} height={height} fill="currentColor" className="text-gray-800" />
      {pieces.map(([phase, kind, off, dur], i) => (
        <rect key={i} x={(off / span) * width} y={0} width={Math.max(0.6, (dur / span) * width)} height={height}
          fill={segmentColor(phase, kind)} fillOpacity={segmentOpacity(phase)}>
          <title>{`${PHASE_LABEL[phase] || phase} · ${KIND_TEXT[kind] || kind} · ${fmtDuration(dur)}`}</title>
        </rect>
      ))}
    </svg>
  );
}
