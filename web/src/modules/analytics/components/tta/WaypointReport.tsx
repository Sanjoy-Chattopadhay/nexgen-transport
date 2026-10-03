import {
  Clock, Route as RouteIcon, Truck, Moon, AlertTriangle,
  Activity, Gauge, TrendingUp, MapPin,
} from 'lucide-react';
import LeafletTrackMap from './LeafletTrackMap';
import { formatDuration, formatDateTime, formatNumber } from '../../lib/formatters';
import { analyzeWaypoint } from '../../lib/waypointAnalysis';
import type { WaypointLike, WaypointVisit, Zone } from '../../lib/waypointAnalysis';
import { useDrillDown } from '../../context/DrillDownContext';
import { tc } from '../../../../core/theme';

const DOW = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

const ZONE_ICON: Record<Zone, any> = {
  detention: Clock, rest: Moon, anomaly: AlertTriangle, transit: Activity,
};
const SEV_STYLE: Record<string, { bg: string; text: string; ring: string }> = {
  critical: { bg: 'bg-red-500/15', text: 'text-red-400', ring: 'ring-red-500/30' },
  high:     { bg: 'bg-orange-500/15', text: 'text-orange-400', ring: 'ring-orange-500/30' },
  moderate: { bg: 'bg-amber-500/15', text: 'text-amber-400', ring: 'ring-amber-500/30' },
  low:      { bg: 'bg-gray-500/15', text: 'text-gray-400', ring: 'ring-gray-500/30' },
  expected: { bg: 'bg-blue-500/15', text: 'text-blue-400', ring: 'ring-blue-500/30' },
};

function Mini({ label, value, sub, accent = 'text-gray-100', onClick }:
  { label: string; value: React.ReactNode; sub?: string; accent?: string; onClick?: () => void }) {
  return (
    <div onClick={onClick}
      className={`bg-gray-800/50 rounded-lg p-3 ${onClick ? 'cursor-pointer hover:bg-gray-800 transition-colors ring-1 ring-transparent hover:ring-cyan-500/30' : ''}`}>
      <p className="text-xs text-gray-500 mb-1">{label}</p>
      <p className={`text-lg font-bold leading-tight ${accent} ${onClick ? 'underline decoration-dotted underline-offset-4' : ''}`}>{value}</p>
      {sub && <p className="text-xs text-gray-500 mt-0.5">{sub}</p>}
    </div>
  );
}

/** Thin 24-bar standstill fingerprint; night hours (22–06) tint blue. */
function HourFingerprint({ hours, accent }: { hours: number[]; accent: string }) {
  const max = Math.max(...hours, 1);
  return (
    <div>
      <div className="flex items-end gap-[2px] h-20">
        {hours.map((v, h) => {
          const night = h >= 22 || h <= 6;
          const pct = v > 0 ? Math.max(6, (v / max) * 100) : 0;
          return (
            <div key={h} className="flex-1 bg-gray-800 rounded-t-sm flex items-end group relative" style={{ minHeight: 2 }}>
              <div className="w-full rounded-t-sm transition-all"
                style={{ height: `${pct}%`, background: night ? tc('#3b82f6') : accent }} />
              <span className="pointer-events-none absolute -top-6 left-1/2 -translate-x-1/2 whitespace-nowrap
                bg-gray-950 border border-gray-700 text-gray-200 text-xs px-1.5 py-0.5 rounded opacity-0
                group-hover:opacity-100 transition-opacity z-10">
                {String(h).padStart(2, '0')}:00 · {formatDuration(v)}
              </span>
            </div>
          );
        })}
      </div>
      <div className="flex justify-between text-xs text-gray-600 font-mono mt-1">
        <span>00</span><span>06</span><span>12</span><span>18</span><span>23</span>
      </div>
    </div>
  );
}

export default function WaypointReport({ wp, visits }:
  { wp: WaypointLike & Record<string, any>; visits: WaypointVisit[] }) {
  const a = analyzeWaypoint(wp, visits);
  const sev = SEV_STYLE[a.severity] || SEV_STYLE.moderate;
  const { open } = useDrillDown();

  // Drill-down: reveal the actual vehicles/trips behind the "N trips / M vehicles"
  // count as a clickable table — each row opens that trip.
  const openVisits = () => open({
    title: `Vehicles caught at ${wp.s_wpnt}`,
    subtitle: `${a.detained.length} detained · ${a.passThrough.length} passed through`,
    columns: [
      { key: 'vehicle', label: 'Vehicle', render: (r: WaypointVisit) => r.vehicle || '—' },
      { key: 'route', label: 'Route', render: (r: WaypointVisit) => r.route || '—' },
      { key: 'driver', label: 'Driver', render: (r: WaypointVisit) => r.driver || '—' },
      { key: 'first_ping', label: 'Arrived', render: (r: WaypointVisit) => formatDateTime(r.first_ping) },
      { key: 'stopped_min', label: 'Stood here', align: 'right', render: (r: WaypointVisit) => formatDuration(r.stopped_min) },
    ],
    rowLink: (r: WaypointVisit) => r.i_trip_no ? `/trips/${r.i_trip_no}` : null,
    rows: [...a.detained, ...a.passThrough],
    empty: 'No trip-level visits stored for this waypoint.',
  });
  const ZIcon = ZONE_ICON[a.zone];
  const hours = (wp.hour_profile && wp.hour_profile.length === 24) ? wp.hour_profile.map(Number) : new Array(24).fill(0);
  const dow = (wp.dow_profile && wp.dow_profile.length === 7) ? wp.dow_profile.map(Number) : new Array(7).fill(0);
  const dowMax = Math.max(...dow, 1);

  return (
    <div className="space-y-5">
      {/* Verdict banner */}
      <div className={`rounded-lg ring-1 ${sev.ring} ${sev.bg} p-4`}>
        <div className="flex items-start justify-between gap-3 flex-wrap">
          <div className="flex items-start gap-3">
            <div className="mt-0.5 w-9 h-9 rounded-lg flex items-center justify-center shrink-0"
              style={{ background: `${a.color}22` }}>
              <ZIcon className="w-5 h-5" style={{ color: a.color }} />
            </div>
            <div>
              <p className="text-xs uppercase tracking-wide text-gray-400 mb-0.5">Classification</p>
              <p className="text-base font-semibold text-white">
                {a.zoneLabel} · <span className="text-gray-300">{a.headline}</span>
              </p>
              <p className="text-xs text-gray-400 mt-0.5">
                {(a.stoppedShare * 100).toFixed(0)}% stopped · {Math.round(a.nightShare * 100)}% overnight · peak {String(a.peakHour).padStart(2, '0')}:00
              </p>
            </div>
          </div>
          <span className={`px-2.5 py-1 rounded-full text-xs font-semibold ${sev.bg} ${sev.text} ring-1 ${sev.ring}`}>
            {a.severityLabel}
          </span>
        </div>
      </div>

      {/* KPI strip */}
      <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3">
        <Mini label="Standstill" value={formatDuration(wp.stopped_min)} accent="text-amber-400" sub={`${(a.stoppedShare * 100).toFixed(0)}% of time here`} />
        <Mini label="Moving time" value={formatDuration(wp.moving_min)} accent="text-blue-400" sub="rolling through" />
        <Mini label="Stop events" value={wp.stop_events ?? '-'} sub={a.fragmentation >= 5 ? 'fragmented / queue' : 'sustained'} />
        <Mini label="Avg stop" value={formatDuration(wp.avg_stop_min)} sub={`longest ${formatDuration(wp.longest_stop_min)}`} accent="text-gray-100" />
        <Mini label="Trips / vehicles" value={`${wp.total_trips ?? '-'} / ${wp.total_vehicles ?? '-'}`} sub={`${a.detained.length} detained, ${a.passThrough.length} passed · click to list`} onClick={openVisits} />
        <Mini label="Dominant vehicle" value={a.topVehicle || '-'} accent="text-gray-100" sub={a.topVehicle ? `${Math.round(a.topVehicleShare * 100)}% of standstill` : undefined} />
      </div>

      {/* Data reading */}
      <div className="rounded-lg border border-gray-800 bg-gradient-to-br from-gray-800/40 to-gray-900 p-4">
        <div className="flex items-center justify-between mb-2 flex-wrap gap-2">
          <h3 className="text-sm font-semibold text-white flex items-center gap-1.5">
            <Gauge className="w-4 h-4 text-cyan-400" /> Data reading
          </h3>
          <span className="text-xs text-gray-500">derived from stored GPS pattern · no LLM</span>
        </div>
        <div className="space-y-2">
          {a.reading.map((p, i) => (
            <p key={i} className={`text-xs leading-relaxed ${a.pingGap && i === a.reading.length - 1 ? 'text-amber-300/80 flex items-start gap-1.5' : 'text-gray-300'}`}>
              {a.pingGap && i === a.reading.length - 1 && <AlertTriangle className="w-3.5 h-3.5 mt-0.5 shrink-0" />}
              <span>{p}</span>
            </p>
          ))}
        </div>
        <div className="mt-3 pt-3 border-t border-gray-800">
          <p className="text-xs uppercase tracking-wide text-gray-500 mb-1 flex items-center gap-1">
            <TrendingUp className="w-3.5 h-3.5" /> Action
          </p>
          <p className="text-xs text-gray-200 leading-relaxed">{a.action}</p>
        </div>
      </div>

      {/* Fingerprints + map */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <div>
          <p className="text-xs uppercase tracking-wide text-gray-500 mb-2 flex items-center gap-1.5">
            <Clock className="w-3.5 h-3.5" /> Standstill by hour
            <span className="ml-auto normal-case tracking-normal text-gray-600 flex items-center gap-1">
              <span className="inline-block w-2 h-2 rounded-sm bg-blue-500" /> night
            </span>
          </p>
          <HourFingerprint hours={hours} accent={a.color} />
        </div>
        <div>
          <p className="text-xs uppercase tracking-wide text-gray-500 mb-2 flex items-center gap-1.5">
            <Activity className="w-3.5 h-3.5" /> By day of week
          </p>
          <div className="flex items-end gap-1.5 h-20">
            {dow.map((v, d) => (
              <div key={d} className="flex-1 flex flex-col items-center justify-end h-full group relative">
                <div className="w-full rounded-t-sm bg-violet-500/80" style={{ height: `${Math.max(v > 0 ? 6 : 0, (v / dowMax) * 100)}%`, minHeight: v > 0 ? 3 : 0 }} />
              </div>
            ))}
          </div>
          <div className="flex justify-between text-xs text-gray-600 font-mono mt-1">
            {DOW.map(d => <span key={d}>{d[0]}</span>)}
          </div>
        </div>
        <div>
          <p className="text-xs uppercase tracking-wide text-gray-500 mb-2 flex items-center gap-1.5">
            <MapPin className="w-3.5 h-3.5" /> Location
          </p>
          {wp.d_lat && wp.d_long ? (
            <LeafletTrackMap
              points={[
                { lat: Number(wp.d_lat), lng: Number(wp.d_long), moving: false },
                { lat: Number(wp.d_lat) + 0.0001, lng: Number(wp.d_long) + 0.0001, moving: false },
              ]}
              hotZones={[{ lat: Number(wp.d_lat), lng: Number(wp.d_long), minutes: Number(wp.stopped_min) || 1, label: wp.s_wpnt, reason: a.zoneLabel }]}
              height={172}
            />
          ) : <p className="text-xs text-gray-600">No coordinates stored.</p>}
        </div>
      </div>

      {/* Trips through this waypoint */}
      <div>
        <h3 className="text-sm font-semibold text-gray-300 mb-2 flex items-center gap-2">
          <RouteIcon className="w-4 h-4 text-blue-400" /> Vehicles caught here
          <span className="text-xs text-gray-600 font-normal">· detained vs passed through</span>
        </h3>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs uppercase tracking-wide text-gray-500 border-b border-gray-800">
                <th className="py-2 pr-3 font-medium">Vehicle</th>
                <th className="py-2 pr-3 font-medium">Route</th>
                <th className="py-2 pr-3 font-medium">Driver</th>
                <th className="py-2 pr-3 font-medium">Arrived</th>
                <th className="py-2 pr-3 font-medium text-right">Stood here</th>
              </tr>
            </thead>
            <tbody>
              {[...a.detained, ...a.passThrough].map((v, i) => {
                const isThru = a.passThrough.includes(v);
                return (
                  <tr key={i} className={`border-b border-gray-800/60 ${isThru ? 'text-gray-600' : 'text-gray-300'}`}>
                    <td className="py-2 pr-3 font-mono font-medium flex items-center gap-1.5">
                      <Truck className={`w-3.5 h-3.5 ${isThru ? 'text-gray-700' : 'text-gray-500'}`} />{v.vehicle || '-'}
                    </td>
                    <td className="py-2 pr-3">{v.route || '-'}</td>
                    <td className="py-2 pr-3">{v.driver || <span className="text-gray-600 italic">unattributed</span>}</td>
                    <td className="py-2 pr-3">{formatDateTime(v.first_ping)}</td>
                    <td className={`py-2 pr-3 text-right font-semibold tabular-nums ${isThru ? 'text-gray-600' : 'text-amber-400'}`}>
                      {isThru ? `passed · ${formatDuration(v.stopped_min)}` : formatDuration(v.stopped_min)}
                    </td>
                  </tr>
                );
              })}
              {!visits.length && (
                <tr><td colSpan={5} className="py-4 text-center text-gray-600 text-xs">No trip-level visits stored.</td></tr>
              )}
            </tbody>
          </table>
        </div>
        <p className="text-xs text-gray-600 mt-2">
          {formatNumber(wp.total_pings)} GPS pings analysed · pattern refreshed {formatDateTime(wp.last_refreshed)}
        </p>
      </div>
    </div>
  );
}
