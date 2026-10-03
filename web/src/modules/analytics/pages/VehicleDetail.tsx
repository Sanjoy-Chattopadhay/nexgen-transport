import { useParams, useNavigate } from 'react-router-dom';
import { ArrowLeft, Truck, Users, Gauge, Target, Route, BarChart3 } from 'lucide-react';
import PageContainer from '../components/layout/PageContainer';
import KPICard from '../components/ui/KPICard';
import Badge from '../components/ui/Badge';
import Spinner from '../components/ui/Spinner';
import { useApi } from '../hooks/useApi';
import { getVehicleDetail } from '../services/vehicles';
import { formatNumber, formatPercent, formatSpeed, formatDistance, formatDuration, formatDateTime, rateKpiColor, rateBadgeVariant } from '../lib/formatters';
import type { VehicleDetail as VehicleDetailType } from '../types/vehicle';

export default function VehicleDetail() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const vehicleId = Number(id);
  const { data, loading } = useApi<VehicleDetailType>(() => getVehicleDetail(vehicleId), [vehicleId]);

  if (loading) return <Spinner />;
  if (!data) return <p className="text-gray-500">Vehicle not found</p>;

  const s = data.summary;

  return (
    <PageContainer title="">
      <button onClick={() => navigate('/vehicles')} className="flex items-center gap-2 text-gray-400 hover:text-white mb-4 text-sm">
        <ArrowLeft className="w-4 h-4" /> Back to Vehicles
      </button>
      <div className="flex items-center gap-3 mb-6">
        <h1 className="text-2xl font-bold text-white">{s.asset_id}</h1>
        <Badge label={s.asset_type} variant="info" />
      </div>

      {/* KPI Cards */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
        <KPICard label="Total Trips" value={formatNumber(s.total_trips)} icon={Truck} color="blue" />
        <KPICard label="Drivers Used" value={formatNumber(s.drivers_used)} icon={Users} color="green" />
        <KPICard label="Avg Speed" value={formatSpeed(s.avg_speed_kmph)} icon={Gauge} color="cyan" />
        <KPICard label="ETA Rate" value={formatPercent(s.eta_success_rate)} icon={Target} color={rateKpiColor(s.eta_success_rate, 60, 40)} />
      </div>

      {/* Distance Summary + Drivers */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-6">
        <div className="bg-gray-900 rounded-xl border border-gray-800 p-5">
          <h2 className="text-lg font-semibold text-white mb-4">Distance Summary</h2>
          <div className="space-y-4 text-sm">
            <div className="flex justify-between"><span className="text-gray-500">Total Distance</span><span className="text-gray-200 font-semibold">{formatDistance(s.total_distance_km)}</span></div>
            <div className="flex justify-between"><span className="text-gray-500">Avg per Trip</span><span className="text-gray-200 font-semibold">{formatDistance(s.avg_distance_km)}</span></div>
            <div className="flex justify-between"><span className="text-gray-500">Avg Speed</span><span className="text-gray-200 font-semibold">{formatSpeed(s.avg_speed_kmph)}</span></div>
            <div className="flex justify-between"><span className="text-gray-500">ETA Success Rate</span><span className="text-gray-200 font-semibold">{formatPercent(s.eta_success_rate)}</span></div>
          </div>
        </div>

        <div className="bg-gray-900 rounded-xl border border-gray-800 p-5">
          <h2 className="text-lg font-semibold text-white mb-4 flex items-center gap-2"><Users className="w-4 h-4 text-green-400" /> Drivers</h2>
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-gray-800">
                <th className="px-3 py-2 text-left text-xs text-gray-400">#</th>
                <th className="px-3 py-2 text-left text-xs text-gray-400">Driver</th>
                <th className="px-3 py-2 text-right text-xs text-gray-400">Trips</th>
              </tr>
            </thead>
            <tbody>
              {data.drivers_used.map((d, i) => (
                <tr key={d.driver_id} onClick={() => navigate(`/drivers/${d.driver_id}`)}
                  className="border-b border-gray-800/50 hover:bg-gray-800/50 cursor-pointer transition-colors">
                  <td className="px-3 py-2 text-gray-500">{i + 1}</td>
                  <td className="px-3 py-2 text-gray-200">{d.driver_name}</td>
                  <td className="px-3 py-2 text-right text-gray-300">{formatNumber(d.trip_count)}</td>
                </tr>
              ))}
              {data.drivers_used.length === 0 && (
                <tr><td colSpan={3} className="px-3 py-6 text-center text-gray-500">No driver data</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* Route Performance vs Fleet Average */}
      {data.vehicle_routes && data.vehicle_routes.length > 0 && (
        <div className="bg-gray-900 rounded-xl border border-gray-800 p-5 mb-6">
          <h2 className="text-lg font-semibold text-white mb-4 flex items-center gap-2"><Route className="w-4 h-4 text-amber-400" /> Route Performance vs Fleet Average</h2>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-gray-800">
                  <th className="px-3 py-2 text-left text-xs text-gray-400">Route</th>
                  <th className="px-3 py-2 text-right text-xs text-gray-400">Trips</th>
                  <th className="px-3 py-2 text-right text-xs text-gray-400">Avg Duration</th>
                  <th className="px-3 py-2 text-right text-xs text-gray-400">Fleet Avg</th>
                  <th className="px-3 py-2 text-right text-xs text-gray-400">Avg Speed</th>
                  <th className="px-3 py-2 text-right text-xs text-gray-400">Fleet Avg</th>
                  <th className="px-3 py-2 text-right text-xs text-gray-400">ETA Rate</th>
                  <th className="px-3 py-2 text-right text-xs text-gray-400">Fleet Avg</th>
                </tr>
              </thead>
              <tbody>
                {data.vehicle_routes.map((vr, i) => {
                  const routeKey = `${vr.origin} -> ${vr.destination}`;
                  const bench = data.route_benchmarks?.[routeKey];
                  const durDiff = bench && vr.avg_duration_min && bench.avg_duration_min
                    ? vr.avg_duration_min - bench.avg_duration_min : null;
                  // Both sides must be guarded: eta_success_rate is null when
                  // no trip on the route has a known outcome, and in JS
                  // `null - 85.5` is -85.5 — a red "massively behind fleet"
                  // badge for a route we simply have no outcome data for.
                  const etaDiff = bench && bench.eta_success_rate != null
                    && vr.eta_success_rate != null
                    ? vr.eta_success_rate - bench.eta_success_rate : null;
                  return (
                    <tr key={i} className="border-b border-gray-800/50">
                      <td className="px-3 py-2 text-gray-200">{vr.origin} → {vr.destination}</td>
                      <td className="px-3 py-2 text-right text-gray-300">{vr.trip_count}</td>
                      <td className="px-3 py-2 text-right text-gray-300">{formatDuration(vr.avg_duration_min)}</td>
                      <td className="px-3 py-2 text-right">
                        {bench ? (
                          <span className="text-gray-500">
                            {formatDuration(bench.avg_duration_min)}
                            {durDiff != null && (
                              <span className={`ml-1 text-xs ${durDiff > 0 ? 'text-red-400' : 'text-green-400'}`}>
                                ({durDiff > 0 ? '+' : ''}{durDiff.toFixed(0)}m)
                              </span>
                            )}
                          </span>
                        ) : <span className="text-gray-600">-</span>}
                      </td>
                      <td className="px-3 py-2 text-right text-gray-300">{formatSpeed(vr.avg_speed_kmph)}</td>
                      <td className="px-3 py-2 text-right text-gray-500">{bench ? formatSpeed(bench.avg_speed_kmph) : '-'}</td>
                      <td className="px-3 py-2 text-right text-gray-300">{formatPercent(vr.eta_success_rate)}</td>
                      <td className="px-3 py-2 text-right">
                        {bench ? (
                          <span className="text-gray-500">
                            {formatPercent(bench.eta_success_rate)}
                            {etaDiff != null && (
                              <span className={`ml-1 text-xs ${etaDiff >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                                ({etaDiff > 0 ? '+' : ''}{etaDiff.toFixed(1)}%)
                              </span>
                            )}
                          </span>
                        ) : <span className="text-gray-600">-</span>}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Monthly Trend */}
      {data.monthly_trend && data.monthly_trend.length > 0 && (
        <div className="bg-gray-900 rounded-xl border border-gray-800 p-5 mb-6">
          <h2 className="text-lg font-semibold text-white mb-4 flex items-center gap-2"><BarChart3 className="w-4 h-4 text-purple-400" /> Monthly Performance (Last 12 Months)</h2>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-gray-800">
                  <th className="px-3 py-2 text-left text-xs text-gray-400">Month</th>
                  <th className="px-3 py-2 text-right text-xs text-gray-400">Trips</th>
                  <th className="px-3 py-2 text-right text-xs text-gray-400">Total KM</th>
                  <th className="px-3 py-2 text-right text-xs text-gray-400">Avg Speed</th>
                  <th className="px-3 py-2 text-right text-xs text-gray-400">ETA Rate</th>
                </tr>
              </thead>
              <tbody>
                {data.monthly_trend.map((m, i) => (
                  <tr key={i} className="border-b border-gray-800/50">
                    <td className="px-3 py-2 text-gray-200">{m.month}</td>
                    <td className="px-3 py-2 text-right text-gray-300">{m.trips}</td>
                    <td className="px-3 py-2 text-right text-gray-300">{formatDistance(m.total_km)}</td>
                    <td className="px-3 py-2 text-right text-gray-300">{formatSpeed(m.avg_speed)}</td>
                    <td className="px-3 py-2 text-right">
                      <Badge label={formatPercent(m.eta_rate)} variant={rateBadgeVariant(m.eta_rate, 60, 40)} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Recent Trips */}
      <div className="bg-gray-900 rounded-xl border border-gray-800 p-5">
        <h2 className="text-lg font-semibold text-white mb-4">Recent Trips</h2>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-gray-800">
                <th className="px-3 py-2 text-left text-xs text-gray-400">Dispatch#</th>
                <th className="px-3 py-2 text-left text-xs text-gray-400">Driver</th>
                <th className="px-3 py-2 text-left text-xs text-gray-400">Origin</th>
                <th className="px-3 py-2 text-left text-xs text-gray-400">Destination</th>
                <th className="px-3 py-2 text-left text-xs text-gray-400">Start</th>
                <th className="px-3 py-2 text-right text-xs text-gray-400">Duration</th>
                <th className="px-3 py-2 text-center text-xs text-gray-400">ETA Met</th>
                <th className="px-3 py-2 text-right text-xs text-gray-400">Speed</th>
              </tr>
            </thead>
            <tbody>
              {data.recent_trips.map(t => (
                <tr key={t.id} onClick={() => navigate(`/trips/${t.id}`)}
                  className="border-b border-gray-800/50 hover:bg-gray-800/50 cursor-pointer transition-colors">
                  <td className="px-3 py-2 text-gray-300">{t.dispatch_entry_no}</td>
                  <td className="px-3 py-2 text-gray-300">{t.driver_name}</td>
                  <td className="px-3 py-2 text-gray-300">{t.origin_name}</td>
                  <td className="px-3 py-2 text-gray-300">{t.destination_name}</td>
                  <td className="px-3 py-2 text-gray-400">{formatDateTime(t.trip_start)}</td>
                  <td className="px-3 py-2 text-right text-gray-300">{formatDuration(t.trip_duration_minutes)}</td>
                  <td className="px-3 py-2 text-center"><Badge label={t.eta_met ? 'Yes' : 'No'} variant={t.eta_met ? 'success' : 'danger'} /></td>
                  <td className="px-3 py-2 text-right text-gray-300">{formatSpeed(t.avg_speed_kmph)}</td>
                </tr>
              ))}
              {data.recent_trips.length === 0 && (
                <tr><td colSpan={8} className="px-3 py-6 text-center text-gray-500">No recent trips</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </PageContainer>
  );
}
