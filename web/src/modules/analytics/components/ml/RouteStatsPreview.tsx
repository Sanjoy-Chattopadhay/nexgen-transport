import { useEffect, useState } from 'react';
import { Clock, Gauge, Target, TrendingUp, Route } from 'lucide-react';
import { getRouteStats } from '../../services/locations';
import { formatDuration, formatDistance, formatSpeed, formatPercent } from '../../lib/formatters';

interface Props {
  origin: string;
  destination: string;
}

export default function RouteStatsPreview({ origin, destination }: Props) {
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    if (!origin || !destination) { setData(null); return; }
    let cancelled = false;
    setLoading(true);
    setError('');
    getRouteStats(origin, destination)
      .then(res => { if (!cancelled) setData(res.data); })
      .catch(() => { if (!cancelled) setError('Could not load route stats'); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [origin, destination]);

  if (!origin || !destination) return null;
  if (loading) return (
    <div className="bg-gray-900/60 rounded-xl border border-gray-800 p-4 animate-shimmer">
      <div className="flex items-center gap-2 text-gray-500 text-sm">
        <Route className="w-4 h-4 animate-spin" /> Loading route data...
      </div>
    </div>
  );
  if (error) return (
    <div className="bg-gray-900/60 rounded-xl border border-gray-800 p-4">
      <p className="text-sm text-gray-500">{error}</p>
    </div>
  );
  if (!data) return null;

  const rs = data.route_summary;
  const hasData = data.total_trips > 0;

  return (
    <div className="bg-gradient-to-br from-gray-900 to-gray-900/80 rounded-xl border border-gray-700/60 overflow-hidden animate-scale-in">
      {/* Header */}
      <div className="px-4 py-3 border-b border-gray-800 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Route className="w-4 h-4 text-amber-400" />
          <span className="text-sm font-semibold text-white">{origin} → {destination}</span>
        </div>
        <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${
          hasData ? 'bg-emerald-500/20 text-emerald-400' : 'bg-amber-500/20 text-amber-400'
        }`}>
          {data.total_trips} past trips
        </span>
      </div>

      {hasData && rs ? (
        <>
          {/* KPI row */}
          <div className="grid grid-cols-4 gap-px bg-gray-800">
            {[
              { label: 'Avg Duration', value: formatDuration(rs.avg_duration_min), icon: Clock, color: 'text-purple-400' },
              { label: 'Avg Distance', value: formatDistance(rs.avg_distance_km), icon: TrendingUp, color: 'text-blue-400' },
              { label: 'Avg Speed', value: formatSpeed(rs.avg_speed_kmph), icon: Gauge, color: 'text-cyan-400' },
              { label: 'ETA Success', value: formatPercent(rs.eta_success_rate), icon: Target, color: rs.eta_success_rate >= 60 ? 'text-emerald-400' : rs.eta_success_rate >= 40 ? 'text-amber-400' : 'text-red-400' },
            ].map((kpi, i) => (
              <div key={i} className={`bg-gray-900 p-3 animate-fade-in-up stagger-${i + 1}`}>
                <div className="flex items-center gap-1.5 mb-1">
                  <kpi.icon className={`w-3 h-3 ${kpi.color}`} />
                  <span className="text-xs text-gray-500 uppercase tracking-wider">{kpi.label}</span>
                </div>
                <p className={`text-sm font-bold ${kpi.color}`}>{kpi.value}</p>
              </div>
            ))}
          </div>

          {/* Extremes */}
          {(data.fastest_minutes || data.slowest_minutes) && (
            <div className="px-4 py-2 flex items-center gap-4 text-xs text-gray-500 border-t border-gray-800/50">
              {data.fastest_minutes && <span>Fastest: <span className="text-emerald-400 font-medium">{formatDuration(data.fastest_minutes)}</span></span>}
              {data.slowest_minutes && <span>Slowest: <span className="text-red-400 font-medium">{formatDuration(data.slowest_minutes)}</span></span>}
              {data.duration_stddev && <span>Std Dev: <span className="text-gray-400 font-medium">{Math.round(data.duration_stddev)}m</span></span>}
            </div>
          )}

          {/* Recent trips */}
          {data.recent_trips && data.recent_trips.length > 0 && (
            <div className="border-t border-gray-800">
              <div className="px-4 py-2">
                <p className="text-xs text-gray-500 uppercase tracking-wider mb-2">Last {data.recent_trips.length} Trips</p>
                <div className="space-y-1">
                  {data.recent_trips.map((t: any, i: number) => (
                    <div key={i} className={`flex items-center justify-between text-xs py-1 animate-fade-in stagger-${i + 1}`}>
                      <span className="text-gray-400 w-28 truncate">{t.driver_name}</span>
                      <span className="text-gray-500">{formatDuration(t.trip_duration_minutes)}</span>
                      <span className="text-gray-500">{formatSpeed(t.avg_speed_kmph)}</span>
                      <span className={`text-xs font-medium ${t.eta_met ? 'text-emerald-400' : 'text-red-400'}`}>
                        {t.eta_met ? 'On Time' : 'Late'}
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          )}
        </>
      ) : (
        <div className="p-4">
          {data.distance_estimate ? (
            <div className="space-y-3">
              <div className="flex items-center gap-2 mb-2">
                <TrendingUp className="w-4 h-4 text-amber-400" />
                <span className="text-sm font-medium text-amber-300">No past trips — Estimated via OpenStreetMap</span>
              </div>
              <div className="grid grid-cols-3 gap-3">
                <div className="bg-gray-800/60 rounded-lg p-3">
                  <p className="text-xs text-gray-500 uppercase tracking-wider mb-1">Distance</p>
                  <p className="text-base font-bold text-blue-400">{data.distance_estimate.distance_km} km</p>
                </div>
                <div className="bg-gray-800/60 rounded-lg p-3">
                  <p className="text-xs text-gray-500 uppercase tracking-wider mb-1">Est. Duration (truck)</p>
                  <p className="text-base font-bold text-purple-400">{formatDuration(data.distance_estimate.estimated_truck_duration_min)}</p>
                </div>
                <div className="bg-gray-800/60 rounded-lg p-3">
                  <p className="text-xs text-gray-500 uppercase tracking-wider mb-1">Assumed Speed</p>
                  <p className="text-base font-bold text-cyan-400">{data.distance_estimate.truck_speed_assumed_kmph} km/h</p>
                </div>
              </div>
              <p className="text-xs text-gray-600">Source: OSRM open-source routing. Truck estimate uses {data.distance_estimate.truck_speed_assumed_kmph} km/h avg speed.</p>
            </div>
          ) : (
            <div className="text-center">
              <p className="text-sm text-gray-500">No historical data for this route.</p>
              <p className="text-xs text-gray-600 mt-1">The prediction will use fleet-wide averages as baseline.</p>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
