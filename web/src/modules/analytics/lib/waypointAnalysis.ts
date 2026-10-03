import { formatDuration } from './formatters';
import { tc } from '../../../core/theme';

/**
 * Waypoint behaviour analysis — turns the stored pattern (from tta_waypoint_stats)
 * plus per-trip visits into a data-scientist-style read: a classification, the
 * derived signals behind it, and plain-language reading + action lines.
 *
 * Everything here is derived from the data, so it works for ANY waypoint, not a
 * hard-coded set. The classification separates the two phenomena that a raw
 * standstill number conflates: controllable detention vs. expected overnight rest.
 */

export type Zone = 'detention' | 'rest' | 'anomaly' | 'transit';
export type Severity = 'critical' | 'high' | 'moderate' | 'low' | 'expected';

export interface WaypointVisit {
  i_trip_no: number;
  vehicle: string | null;
  route?: string;
  driver?: string | null;
  consignor?: string | null;
  first_ping?: string;
  last_ping?: string;
  pings?: number;
  stopped_min?: number;
  moving_min?: number;
}

export interface WaypointLike {
  s_wpnt: string;
  s_state?: string;
  stopped_min?: number;
  moving_min?: number;
  stop_events?: number;
  avg_stop_min?: number;
  longest_stop_min?: number;
  total_trips?: number;
  total_vehicles?: number;
  total_pings?: number;
  hour_profile?: number[] | null;
  dow_profile?: number[] | null;
}

export interface WaypointAnalysis {
  zone: Zone;
  zoneLabel: string;
  color: string;               // hex accent for the zone
  severity: Severity;
  severityLabel: string;
  stoppedShare: number;        // 0..1
  nightShare: number;          // 0..1, standstill in 22:00–06:00
  peakHour: number;            // 0..23
  activeHours: number;         // count of hours with any standstill
  fragmentation: number;       // stop_events
  detained: WaypointVisit[];   // visits that actually halted here
  passThrough: WaypointVisit[];// visits that rolled through
  topVehicle: string | null;
  topVehicleShare: number;     // 0..1 of standstill from the single busiest vehicle
  pingGap: boolean;            // longest single halt exceeds gap-capped total → undercount
  headline: string;
  reading: string[];           // ordered paragraphs
  action: string;
}

const ZONE_META: Record<Zone, { label: string; color: string }> = {
  detention: { label: 'Sustained Detention', color: tc('#f59e0b') }, // amber
  rest:      { label: 'Overnight Rest',       color: tc('#3b82f6') }, // blue
  anomaly:   { label: 'Extended Halt · Review', color: tc('#ef4444') }, // red
  transit:   { label: 'Transit Halt',         color: tc('#06b6d4') }, // cyan
};

const NIGHT_HOURS = [22, 23, 0, 1, 2, 3, 4, 5];

function sum(a: number[]): number { return a.reduce((s, n) => s + (Number(n) || 0), 0); }

/** Detain threshold: a visit counts as "detained" if it stood ≥30 min AND ≥15%
 *  of the waypoint's total standstill — otherwise it just passed through. */
function splitVisits(visits: WaypointVisit[], totalStopped: number) {
  const floor = Math.max(30, totalStopped * 0.15);
  const detained: WaypointVisit[] = [];
  const passThrough: WaypointVisit[] = [];
  for (const v of visits) {
    if ((Number(v.stopped_min) || 0) >= floor) detained.push(v);
    else passThrough.push(v);
  }
  // guard: if nothing cleared the floor, treat the single longest as detained
  if (!detained.length && visits.length) {
    const sorted = [...visits].sort((a, b) => (b.stopped_min || 0) - (a.stopped_min || 0));
    detained.push(sorted[0]);
    return { detained, passThrough: sorted.slice(1) };
  }
  return { detained, passThrough };
}

export function analyzeWaypoint(wp: WaypointLike, visits: WaypointVisit[] = []): WaypointAnalysis {
  const stopped = Number(wp.stopped_min) || 0;
  const moving = Number(wp.moving_min) || 0;
  const stoppedShare = stopped + moving > 0 ? stopped / (stopped + moving) : 0;
  const hours = (wp.hour_profile && wp.hour_profile.length === 24) ? wp.hour_profile.map(Number) : new Array(24).fill(0);
  const hoursTotal = sum(hours) || 1;
  const nightShare = sum(NIGHT_HOURS.map(h => hours[h])) / hoursTotal;
  const peakHour = hours.indexOf(Math.max(...hours));
  const activeHours = hours.filter(v => v > 0).length;
  const fragmentation = Number(wp.stop_events) || 0;
  const longest = Number(wp.longest_stop_min) || 0;
  const pingGap = longest > stopped * 1.1 && longest - stopped > 45;

  const { detained, passThrough } = splitVisits(visits, stopped);

  // standstill by vehicle → dominance
  const byVeh = new Map<string, number>();
  for (const v of visits) {
    const key = v.vehicle || '—';
    byVeh.set(key, (byVeh.get(key) || 0) + (Number(v.stopped_min) || 0));
  }
  let topVehicle: string | null = null, topVehMin = 0;
  for (const [k, m] of byVeh) if (m > topVehMin) { topVehMin = m; topVehicle = k; }
  const vehTotal = [...byVeh.values()].reduce((s, n) => s + n, 0) || 1;
  const topVehicleShare = topVehMin / vehTotal;

  // ---- classification (data-driven, priority order) ----
  let zone: Zone;
  if (nightShare >= 0.55) {
    zone = 'rest';                                   // concentrated 22:00–06:00 → sleep
  } else if (fragmentation <= 1 && longest >= 480 && nightShare < 0.4) {
    zone = 'anomaly';                                // one long daytime block, not a rest
  } else if (pingGap && longest >= 480) {
    zone = 'anomaly';                                // undercounted long halt worth a look
  } else if (stoppedShare >= 0.85) {
    zone = 'detention';                              // sustained standstill, controllable
  } else {
    zone = 'transit';                                // mostly rolling through
  }
  const meta = ZONE_META[zone];

  // ---- severity ----
  let severity: Severity, severityLabel: string;
  if (zone === 'rest') { severity = 'expected'; severityLabel = 'Expected'; }
  else if (zone === 'anomaly') { severity = 'high'; severityLabel = 'Needs review'; }
  else if (zone === 'transit') { severity = 'low'; severityLabel = 'Low'; }
  else { // detention scales with lost time
    if (stopped >= 600) { severity = 'critical'; severityLabel = 'Critical'; }
    else if (stopped >= 300) { severity = 'high'; severityLabel = 'High'; }
    else if (stopped >= 120) { severity = 'moderate'; severityLabel = 'Moderate'; }
    else { severity = 'low'; severityLabel = 'Low'; }
  }

  // ---- narrative ----
  const pct = (x: number) => `${Math.round(x * 100)}%`;
  const hh = (h: number) => `${String(h).padStart(2, '0')}:00`;
  const reading: string[] = [];

  // 1. nature of the halt
  let nature = `${(stoppedShare * 100).toFixed(0)}% of tracked time here is motionless.`;
  if (detained.length <= 1 && topVehicle && topVehicleShare >= 0.65) {
    nature += ` The standstill is dominated by a single vehicle — ${topVehicle} accounts for ${pct(topVehicleShare)} of all halt time at this point, so this reads as one stuck truck rather than a shared pattern.`;
  } else if (detained.length >= 2 && topVehicleShare <= 0.7) {
    nature += ` It is shared fairly evenly across ${detained.length} vehicles, which points to a location constraint (throughput / capacity) rather than one truck.`;
  } else if (detained.length >= 2) {
    nature += ` ${detained.length} vehicles halted here, led by ${topVehicle} at ${pct(topVehicleShare)} of the total.`;
  }
  reading.push(nature);

  // 2. timing fingerprint
  let timing: string;
  if (zone === 'rest') {
    timing = `Standstill concentrates overnight — ${pct(nightShare)} of it falls between 22:00 and 06:00, peaking near ${hh(peakHour)}. That is the fingerprint of a driver rest halt, not a facility delay: necessary, and it should not be counted as lost time.`;
  } else if (fragmentation >= 5 && (Number(wp.avg_stop_min) || 0) < stopped / 2) {
    timing = `The halt is fragmented into ${fragmentation} separate stop events, peaking around ${hh(peakHour)} — a stop-start queue (gate / weighbridge) shuffling forward, not a single park.`;
  } else {
    timing = `Standstill sits in ${activeHours <= 8 ? 'a tight' : 'a broad'} window peaking around ${hh(peakHour)}, across ${fragmentation || 'one'} sustained block${fragmentation === 1 ? '' : 's'} — a place trucks wait, not pass.`;
  }
  reading.push(timing);

  // 3. data-quality caveat, only when real
  if (pingGap) {
    reading.push(`Ping-cadence note: the longest single halt (${formatDuration(longest)}) exceeds the gap-capped standstill total (${formatDuration(stopped)}). Sparse GPS pings are undercounting the true dwell — read the standstill figure as a lower bound.`);
  }

  // ---- headline + action ----
  let headline: string, action: string;
  switch (zone) {
    case 'rest':
      headline = `Overnight driver rest — ${formatDuration(longest)} single halt, not detention`;
      action = `Tag as statutory overnight rest and exclude from detention KPIs. Counting it as delay would penalise a compliant driver; its predictability is useful for ETA modelling instead.`;
      break;
    case 'anomaly':
      headline = `${formatDuration(longest)} extended halt outside the normal rest window`;
      action = `Flag for manual review — attach a reason code. ${pingGap ? 'Also fix the ping / gap-cap logic so long sparse-ping halts stop reading short.' : 'An extended daytime halt here is unexplained by the current pattern.'}`;
      break;
    case 'transit':
      headline = `Mostly a through-point — ${(stoppedShare * 100).toFixed(0)}% stopped, ${formatDuration(moving)} moving`;
      action = `Low priority. Useful as a transit-speed reference; monitor in case the stopped share climbs.`;
      break;
    default: // detention
      headline = `${formatDuration(stopped)} of controllable standstill${topVehicle && topVehicleShare >= 0.65 ? `, mostly ${topVehicle}` : ''}`;
      if (detained.length >= 2 && topVehicleShare <= 0.7) {
        action = `Even split across vehicles in a tight window signals a capacity constraint — slot / appointment scheduling is the lever here, not driver behaviour.`;
      } else if (topVehicle && topVehicleShare >= 0.65) {
        action = `Investigate ${topVehicle} specifically — this single vehicle-halt is the bulk of the loss and the highest-leverage thing to physically check.`;
      } else {
        action = `Sustained standstill with controllable cause — review gate / dock throughput in the ${hh(peakHour)} peak window.`;
      }
  }

  return {
    zone, zoneLabel: meta.label, color: meta.color, severity, severityLabel,
    stoppedShare, nightShare, peakHour, activeHours, fragmentation,
    detained, passThrough, topVehicle, topVehicleShare, pingGap,
    headline, reading, action,
  };
}
