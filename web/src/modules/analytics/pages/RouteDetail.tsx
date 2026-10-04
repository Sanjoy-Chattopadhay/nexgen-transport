import { useParams, useNavigate } from 'react-router-dom';
import { ArrowLeft, MapPin, Clock, Target, Hash } from 'lucide-react';
import PageContainer from '../components/layout/PageContainer';
import KPICard from '../components/ui/KPICard';
import { ProofGrid } from '../../../core/proof/ProofPanel';
import HeatmapGrid from '../components/charts/HeatmapGrid';
import Badge from '../components/ui/Badge';
import Spinner from '../components/ui/Spinner';
import { useApi } from '../hooks/useApi';
import { getRouteDetail } from '../services/routes';
import { formatNumber, formatPercent, formatDuration, formatDistance, formatDateTime, formatSpeed, rateKpiColor, rateTextClass } from '../lib/formatters';
import type { RouteDetail as RouteDetailType } from '../types/route';

export default function RouteDetail() {
  const { origin, destination } = useParams<{ origin: string; destination: string }>();
  const navigate = useNavigate();
  const decodedOrigin = decodeURIComponent(origin || '');
  const decodedDest = decodeURIComponent(destination || '');
  const routeParams = { origin: decodedOrigin, destination: decodedDest };
  const num = (v: unknown) => (v == null || v === '' ? null : Number(v));

  const { data, loading } = useApi<RouteDetailType>(
    () => getRouteDetail(decodedOrigin, decodedDest),
    [decodedOrigin, decodedDest]
  );

  if (loading) return <Spinner />;
  if (!data) return <p className="text-gray-500">Route not found</p>;

  const s = data.summary;

  return (
    <PageContainer title="">
      <button onClick={() => navigate('/routes')} className="flex items-center gap-2 text-gray-400 hover:text-white mb-4 text-sm">
        <ArrowLeft className="w-4 h-4" /> Back to Routes
      </button>
      <h1 className="text-2xl font-bold text-white mb-6">{decodedOrigin} → {decodedDest}</h1>

      <ProofGrid className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
        <KPICard label="Trip Count" value={formatNumber(s.trip_count)} icon={Hash} color="blue"
          proof={{ dataset: 'route.trips', params: routeParams, value: num(s.trip_count) }} />
        <KPICard label="Avg Duration" value={formatDuration(s.avg_duration_min)} icon={Clock} color="purple"
          proof={{ dataset: 'route.duration', params: routeParams, value: num(s.avg_duration_min) }} />
        <KPICard label="ETA Rate" value={formatPercent(s.eta_success_rate)} icon={Target} color={rateKpiColor(s.eta_success_rate)}
          proof={{ dataset: 'route.eta', params: routeParams, value: num(s.eta_success_rate) }} />
        <KPICard label="Avg Distance" value={formatDistance(s.avg_distance_km)} icon={MapPin} color="cyan"
          proof={{ dataset: 'route.distance', params: routeParams, value: num(s.avg_distance_km) }} />
      </ProofGrid>

      {data.time_patterns.length > 0 && (
        <div className="bg-gray-900 rounded-xl border border-gray-800 p-5 mb-6">
          <h2 className="text-lg font-semibold text-white mb-4">Time Patterns</h2>
          <HeatmapGrid data={data.time_patterns} valueKey="trip_count" label="Trip Volume by Hour & Day" />
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <div className="bg-gray-900 rounded-xl border border-gray-800 p-5">
          <h2 className="text-lg font-semibold text-white mb-4">Top Drivers on this Route</h2>
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-gray-800">
                <th className="px-3 py-2 text-left text-xs text-gray-400">#</th>
                <th className="px-3 py-2 text-left text-xs text-gray-400">Driver</th>
                <th className="px-3 py-2 text-right text-xs text-gray-400">Trips</th>
                <th className="px-3 py-2 text-right text-xs text-gray-400">Avg Duration</th>
                <th className="px-3 py-2 text-right text-xs text-gray-400">ETA Rate</th>
              </tr>
            </thead>
            <tbody>
              {data.top_drivers.map((d, i) => (
                <tr key={d.driver_id} onClick={() => navigate(`/drivers/${d.driver_id}`)}
                  className="border-b border-gray-800/50 hover:bg-gray-800/50 cursor-pointer transition-colors">
                  <td className="px-3 py-2 text-gray-500">{i + 1}</td>
                  <td className="px-3 py-2 text-gray-200">{d.driver_name}</td>
                  <td className="px-3 py-2 text-right text-gray-300">{formatNumber(d.trip_count)}</td>
                  <td className="px-3 py-2 text-right text-gray-300">{formatDuration(d.avg_duration)}</td>
                  <td className="px-3 py-2 text-right">
                    <span className={rateTextClass(d.eta_rate)}>
                      {formatPercent(d.eta_rate)}
                    </span>
                  </td>
                </tr>
              ))}
              {data.top_drivers.length === 0 && (
                <tr><td colSpan={5} className="px-3 py-6 text-center text-gray-500">No driver data</td></tr>
              )}
            </tbody>
          </table>
        </div>

        <div className="bg-gray-900 rounded-xl border border-gray-800 p-5">
          <h2 className="text-lg font-semibold text-white mb-4">Recent Trips</h2>
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-gray-800">
                <th className="px-3 py-2 text-left text-xs text-gray-400">Dispatch#</th>
                <th className="px-3 py-2 text-left text-xs text-gray-400">Driver</th>
                <th className="px-3 py-2 text-left text-xs text-gray-400">Start</th>
                <th className="px-3 py-2 text-right text-xs text-gray-400">Duration</th>
                <th className="px-3 py-2 text-center text-xs text-gray-400">ETA</th>
                <th className="px-3 py-2 text-right text-xs text-gray-400">Speed</th>
              </tr>
            </thead>
            <tbody>
              {data.recent_trips.map(t => (
                <tr key={t.id} onClick={() => navigate(`/trips/${t.id}`)}
                  className="border-b border-gray-800/50 hover:bg-gray-800/50 cursor-pointer transition-colors">
                  <td className="px-3 py-2 text-gray-300">{t.dispatch_entry_no}</td>
                  <td className="px-3 py-2 text-gray-300">{t.driver_name}</td>
                  <td className="px-3 py-2 text-gray-400">{formatDateTime(t.trip_start)}</td>
                  <td className="px-3 py-2 text-right text-gray-300">{formatDuration(t.trip_duration_minutes)}</td>
                  <td className="px-3 py-2 text-center"><Badge label={t.eta_met ? 'Yes' : 'No'} variant={t.eta_met ? 'success' : 'danger'} /></td>
                  <td className="px-3 py-2 text-right text-gray-300">{formatSpeed(t.avg_speed_kmph)}</td>
                </tr>
              ))}
              {data.recent_trips.length === 0 && (
                <tr><td colSpan={6} className="px-3 py-6 text-center text-gray-500">No recent trips</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </PageContainer>
  );
}
