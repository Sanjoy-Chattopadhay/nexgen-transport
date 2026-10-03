import { useParams, useNavigate, Link } from 'react-router-dom';
import { ArrowLeft, MapPin, Clock, Gauge, Target, User, Truck, Route, TrendingUp } from 'lucide-react';
import PageContainer from '../components/layout/PageContainer';
import KPICard from '../components/ui/KPICard';
import Badge from '../components/ui/Badge';
import Spinner from '../components/ui/Spinner';
import { useApi } from '../hooks/useApi';
import { getTripDetail } from '../services/trips';
import { formatDuration, formatDistance, formatSpeed, formatDateTime, formatNumber, formatPercent } from '../lib/formatters';
import type { TripDetail as TripDetailType } from '../types/trip';

function StatRow({ label, value, compare, unit }: { label: string; value: string | number | null; compare?: number | null; unit?: string }) {
  const val = value ?? '-';
  let compareEl = null;
  if (compare != null && value != null) {
    const diff = Number(value) - compare;
    const pct = compare > 0 ? ((diff / compare) * 100) : 0;
    const color = diff > 0 ? 'text-red-400' : diff < 0 ? 'text-green-400' : 'text-gray-500';
    const sign = diff > 0 ? '+' : '';
    compareEl = <span className={`text-xs ${color} ml-2`}>({sign}{pct.toFixed(1)}% vs route avg)</span>;
  }
  return (
    <div className="flex justify-between items-center">
      <span className="text-gray-500">{label}</span>
      <span className="text-gray-200">{val}{unit ? ` ${unit}` : ''}{compareEl}</span>
    </div>
  );
}

export default function TripDetail() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const tripId = Number(id);
  const { data, loading } = useApi<TripDetailType>(() => getTripDetail(tripId), [tripId]);

  if (loading) return <Spinner />;
  if (!data) return <p className="text-gray-500">Trip not found</p>;

  const t = data.trip;
  const ds = data.driver_stats;
  const rs = data.route_stats;
  const vs = data.vehicle_stats;
  const drs = data.driver_route_stats;

  return (
    <PageContainer title="">
      <button onClick={() => navigate('/trips')} className="flex items-center gap-2 text-gray-400 hover:text-white mb-4 text-sm">
        <ArrowLeft className="w-4 h-4" /> Back to Trips
      </button>
      <h1 className="text-2xl font-bold text-white mb-6">Trip: {t.dispatch_entry_no}</h1>

      {/* KPI Cards */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
        <KPICard label="Duration" value={formatDuration(t.trip_duration_minutes)} icon={Clock} color="purple" />
        <KPICard label="Distance" value={formatDistance(t.trip_km)} icon={MapPin} color="blue" />
        <KPICard label="Avg Speed" value={formatSpeed(t.avg_speed_kmph)} icon={Gauge} color="cyan" />
        <KPICard label="ETA Met" value={t.eta_met ? 'Yes' : 'No'} icon={Target} color={t.eta_met ? 'green' : 'red'} />
      </div>

      {/* Trip Information + ETA Analysis */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-6">
        <div className="bg-gray-900 rounded-xl border border-gray-800 p-5">
          <h2 className="text-lg font-semibold text-white mb-4">Trip Information</h2>
          <div className="space-y-3 text-sm">
            <div className="flex justify-between">
              <span className="text-gray-500">Driver</span>
              <Link to={`/drivers/${t.driver_id}`} className="text-blue-400 hover:underline">{t.driver_name}</Link>
            </div>
            {t.driver_mobile && (
              <div className="flex justify-between">
                <span className="text-gray-500">Mobile</span>
                <span className="text-gray-300">{t.driver_mobile}</span>
              </div>
            )}
            <div className="flex justify-between">
              <span className="text-gray-500">Vehicle</span>
              <Link to={`/vehicles/${t.vehicle_id}`} className="text-blue-400 hover:underline">{t.asset_id} {t.asset_type ? `(${t.asset_type})` : ''}</Link>
            </div>
            <div className="flex justify-between"><span className="text-gray-500">Origin</span><span className="text-gray-300">{t.origin_name}</span></div>
            <div className="flex justify-between"><span className="text-gray-500">Destination</span><span className="text-gray-300">{t.destination_name}</span></div>
            <div className="flex justify-between"><span className="text-gray-500">Customer</span><span className="text-gray-300">{t.customer_name || '-'}</span></div>
            <div className="flex justify-between"><span className="text-gray-500">Status</span><Badge label={t.trip_status} variant={t.trip_status === 'C' ? 'success' : 'info'} /></div>
            <div className="flex justify-between"><span className="text-gray-500">Start</span><span className="text-gray-300">{formatDateTime(t.trip_start)}</span></div>
            <div className="flex justify-between"><span className="text-gray-500">End (ATA)</span><span className="text-gray-300">{formatDateTime(t.trip_end)}</span></div>
            <div className="flex justify-between"><span className="text-gray-500">ETA</span><span className="text-gray-300">{formatDateTime(t.trip_eta)}</span></div>
            {t.trip_close_remark && (
              <div className="flex justify-between"><span className="text-gray-500">Close Remark</span><span className="text-gray-300">{t.trip_close_remark}</span></div>
            )}
            {t.material_desc && (
              <div className="flex justify-between"><span className="text-gray-500">Material</span><span className="text-gray-300 text-right max-w-[200px] truncate">{t.material_desc}</span></div>
            )}
          </div>
        </div>

        {/* ETA / Distance Analysis */}
        <div className="bg-gray-900 rounded-xl border border-gray-800 p-5">
          <h2 className="text-lg font-semibold text-white mb-4 flex items-center gap-2"><TrendingUp className="w-4 h-4 text-purple-400" /> Trip vs Route Average</h2>
          {rs ? (
            <div className="space-y-3 text-sm">
              <StatRow label="Duration" value={t.trip_duration_minutes ? `${formatDuration(t.trip_duration_minutes)}` : null} />
              <StatRow label="Route Avg Duration" value={rs.avg_duration_min ? formatDuration(rs.avg_duration_min) : '-'} />
              <div className="border-t border-gray-800 my-2" />
              <StatRow label="Speed" value={t.avg_speed_kmph?.toFixed(1)} unit="km/h" />
              <StatRow label="Route Avg Speed" value={rs.avg_speed_kmph?.toFixed(1)} unit="km/h" />
              <div className="border-t border-gray-800 my-2" />
              <StatRow label="Distance" value={t.trip_km ? formatDistance(t.trip_km) : '-'} />
              <StatRow label="Route Avg Distance" value={rs.avg_distance_km ? formatDistance(rs.avg_distance_km) : '-'} />
              <div className="border-t border-gray-800 my-2" />
              <StatRow label="Route ETA Success Rate" value={formatPercent(rs.eta_success_rate)} />
              <StatRow label="Total Trips on Route" value={formatNumber(rs.trip_count)} />
            </div>
          ) : (
            <p className="text-gray-500 text-sm">No route summary data available for this route.</p>
          )}
        </div>
      </div>

      {/* Driver Stats + Driver Route History */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-6">
        {ds && (
          <div className="bg-gray-900 rounded-xl border border-gray-800 p-5">
            <h2 className="text-lg font-semibold text-white mb-4 flex items-center gap-2"><User className="w-4 h-4 text-green-400" /> Driver Performance</h2>
            <div className="space-y-3 text-sm">
              <StatRow label="Total Trips" value={formatNumber(ds.total_trips)} />
              <StatRow label="ETA Success Rate" value={formatPercent(ds.eta_success_rate)} />
              <StatRow label="Avg Speed" value={ds.avg_speed_kmph?.toFixed(1)} unit="km/h" />
              <StatRow label="Avg Duration" value={ds.avg_duration_min ? formatDuration(ds.avg_duration_min) : '-'} />
              <StatRow label="Total Distance" value={formatDistance(ds.total_distance_km)} />
              <StatRow label="Vehicles Used" value={formatNumber(ds.vehicles_used)} />
              <StatRow label="Avg ETA Delay" value={ds.avg_eta_delay_min?.toFixed(1)} unit="min" />
            </div>
          </div>
        )}

        {drs && drs.route_trips > 0 && (
          <div className="bg-gray-900 rounded-xl border border-gray-800 p-5">
            <h2 className="text-lg font-semibold text-white mb-4 flex items-center gap-2"><Route className="w-4 h-4 text-amber-400" /> Driver on This Route</h2>
            <p className="text-xs text-gray-500 mb-3">{t.driver_name}'s history on {t.origin_name} → {t.destination_name}</p>
            <div className="space-y-3 text-sm">
              <StatRow label="Trips on Route" value={formatNumber(drs.route_trips)} />
              <StatRow label="ETA Success Rate" value={formatPercent(drs.eta_success_rate)} />
              <StatRow label="Avg Duration" value={drs.avg_duration_min ? formatDuration(drs.avg_duration_min) : '-'} />
              <StatRow label="Avg Speed" value={drs.avg_speed_kmph?.toFixed(1)} unit="km/h" />
              <StatRow label="Avg Distance" value={drs.avg_distance_km ? formatDistance(drs.avg_distance_km) : '-'} />
            </div>
          </div>
        )}

        {vs && !drs && (
          <div className="bg-gray-900 rounded-xl border border-gray-800 p-5">
            <h2 className="text-lg font-semibold text-white mb-4 flex items-center gap-2"><Truck className="w-4 h-4 text-cyan-400" /> Vehicle Performance</h2>
            <div className="space-y-3 text-sm">
              <StatRow label="Total Trips" value={formatNumber(vs.total_trips)} />
              <StatRow label="ETA Success Rate" value={formatPercent(vs.eta_success_rate)} />
              <StatRow label="Avg Speed" value={vs.avg_speed_kmph?.toFixed(1)} unit="km/h" />
              <StatRow label="Total Distance" value={formatDistance(vs.total_distance_km)} />
              <StatRow label="Drivers Used" value={formatNumber(vs.drivers_used)} />
            </div>
          </div>
        )}
      </div>

      {/* Recent Trips on Same Route */}
      {data.route_recent_trips && data.route_recent_trips.length > 0 && (
        <div className="bg-gray-900 rounded-xl border border-gray-800 p-5 mb-6">
          <h2 className="text-lg font-semibold text-white mb-4">Recent Trips on Same Route ({t.origin_name} → {t.destination_name})</h2>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-gray-800">
                  <th className="px-3 py-2 text-left text-xs text-gray-400">Dispatch#</th>
                  <th className="px-3 py-2 text-left text-xs text-gray-400">Driver</th>
                  <th className="px-3 py-2 text-left text-xs text-gray-400">Start</th>
                  <th className="px-3 py-2 text-right text-xs text-gray-400">Duration</th>
                  <th className="px-3 py-2 text-right text-xs text-gray-400">Speed</th>
                  <th className="px-3 py-2 text-center text-xs text-gray-400">ETA Met</th>
                </tr>
              </thead>
              <tbody>
                {data.route_recent_trips.map(rt => (
                  <tr key={rt.id} onClick={() => navigate(`/trips/${rt.id}`)}
                    className="border-b border-gray-800/50 hover:bg-gray-800/50 cursor-pointer transition-colors">
                    <td className="px-3 py-2 text-gray-300">{rt.dispatch_entry_no}</td>
                    <td className="px-3 py-2 text-gray-300">{rt.driver_name}</td>
                    <td className="px-3 py-2 text-gray-400">{formatDateTime(rt.trip_start)}</td>
                    <td className="px-3 py-2 text-right text-gray-300">{formatDuration(rt.trip_duration_minutes)}</td>
                    <td className="px-3 py-2 text-right text-gray-300">{formatSpeed(rt.avg_speed_kmph)}</td>
                    <td className="px-3 py-2 text-center"><Badge label={rt.eta_met ? 'Yes' : 'No'} variant={rt.eta_met ? 'success' : 'danger'} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Waypoints */}
      <div className="bg-gray-900 rounded-xl border border-gray-800 p-5">
        <h2 className="text-lg font-semibold text-white mb-4">Waypoints ({data.waypoints.length})</h2>
        {data.waypoints.length === 0 ? (
          <p className="text-gray-500 text-sm">No waypoints recorded for this trip</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-gray-800">
                  <th className="px-3 py-2 text-left text-xs text-gray-400">Time</th>
                  <th className="px-3 py-2 text-left text-xs text-gray-400">Location</th>
                  <th className="px-3 py-2 text-right text-xs text-gray-400">Speed</th>
                  <th className="px-3 py-2 text-left text-xs text-gray-400">Status</th>
                  <th className="px-3 py-2 text-right text-xs text-gray-400">Dist from Prev</th>
                </tr>
              </thead>
              <tbody>
                {data.waypoints.map((w, i) => (
                  <tr key={i} className="border-b border-gray-800/50">
                    <td className="px-3 py-2 text-gray-400">{formatDateTime(w.recorded_at)}</td>
                    <td className="px-3 py-2 text-gray-300">{w.location_text || '-'}</td>
                    <td className="px-3 py-2 text-right text-gray-300">{formatSpeed(w.speed_kmph)}</td>
                    <td className="px-3 py-2 text-gray-300">{w.status || '-'}</td>
                    <td className="px-3 py-2 text-right text-gray-300">{formatDistance(w.distance_from_prev)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </PageContainer>
  );
}
