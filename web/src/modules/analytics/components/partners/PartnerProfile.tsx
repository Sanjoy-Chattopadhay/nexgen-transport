import { useNavigate } from 'react-router-dom';
import {
  ResponsiveContainer, ComposedChart, Bar, Line, XAxis, YAxis, CartesianGrid, Tooltip,
} from 'recharts';
import { MapPin, Target, Gauge, Clock, Truck, Users, Route as RouteIcon, ChevronRight } from 'lucide-react';
import KPICard from '../ui/KPICard';
import Badge from '../ui/Badge';
import ChartCard from '../ui/ChartCard';
import DonutChart from '../charts/DonutChart';
import { CHART_COLORS } from '../../lib/colors';
import { formatNumber, formatPercent, formatSpeed, formatDuration, formatDistance, formatDateTime } from '../../lib/formatters';
import type { PartnerDetail } from '../../services/partners';
import { tc } from '../../../../core/theme';

const TOOLTIP = { backgroundColor: CHART_COLORS.tooltipBg, border: `1px solid ${tc('#374151')}`, borderRadius: 8, color: tc('#f3f4f6') };
const DELIVERY_COLORS: Record<string, string> = {
  'On Time Delivery': tc('#10b981'), 'Early Delivery': '#22c55e',
  'Delayed Delivery': tc('#ef4444'), 'Late Delivery': tc('#ef4444'), Unknown: tc('#6b7280'),
};

const otdClass = (v: number | null) =>
  v == null ? 'text-gray-500' : v >= 95 ? 'text-emerald-400' : v >= 80 ? 'text-amber-400' : 'text-red-400';

export default function PartnerProfile({ detail }: { detail: PartnerDetail }) {
  const navigate = useNavigate();
  const k = detail.kpis;

  const delivery = detail.delivery.map(d => ({
    name: d.status, value: d.trips, color: DELIVERY_COLORS[d.status] ?? tc('#3b82f6'),
  }));

  return (
    <>
      {/* KPIs */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
        <KPICard label="Total Trips" value={formatNumber(k.trips)} icon={MapPin} color="blue" />
        <KPICard label="On-Time %" value={formatPercent(k.otd_pct)} icon={Target}
          color={(k.otd_pct ?? 0) >= 90 ? 'green' : (k.otd_pct ?? 0) >= 80 ? 'amber' : 'red'} />
        <KPICard label="Distance" value={formatDistance(k.total_km)} icon={Truck} color="purple" />
        <KPICard label="Avg Speed" value={formatSpeed(k.avg_speed)} icon={Gauge} color="cyan" />
      </div>
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
        <MiniStat icon={Clock} label="Avg Transit" value={formatDuration(k.avg_duration_min)} />
        <MiniStat icon={RouteIcon} label="Destinations" value={formatNumber(k.destinations)} />
        <MiniStat icon={Truck} label="Vehicles" value={formatNumber(k.vehicles)} />
        <MiniStat icon={Users} label="Drivers" value={formatNumber(k.drivers)} />
      </div>

      {/* Trend + delivery split */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6 mb-6">
        <ChartCard title="Monthly Trend"
      method={{
        formula: "This partner's trips per calendar month with the service metric overlaid.",
        plot: "dualAxis",
      }} icon={RouteIcon} className="lg:col-span-2"
          explain="Trips and on-time performance over time.">
          {detail.monthly.length ? (
            <ResponsiveContainer width="100%" height={280}>
              <ComposedChart data={detail.monthly}>
                <CartesianGrid strokeDasharray="3 3" stroke={CHART_COLORS.grid} />
                <XAxis dataKey="month" tick={{ fill: tc('#9ca3af'), fontSize: 12 }} axisLine={false} tickLine={false} />
                <YAxis yAxisId="l" tick={{ fill: tc('#9ca3af'), fontSize: 12 }} axisLine={false} tickLine={false} />
                <YAxis yAxisId="r" orientation="right" domain={[0, 100]} tick={{ fill: tc('#9ca3af'), fontSize: 12 }} axisLine={false} tickLine={false} />
                <Tooltip contentStyle={TOOLTIP} />
                <Bar yAxisId="l" dataKey="trips" name="Trips" fill={tc(tc('#3b82f6'))} radius={[4, 4, 0, 0]} />
                <Line yAxisId="r" type="monotone" dataKey="otd_pct" name="OTD %" stroke={tc(tc('#10b981'))} strokeWidth={2} dot={{ r: 3 }} />
              </ComposedChart>
            </ResponsiveContainer>
          ) : <Empty />}
        </ChartCard>
        <ChartCard title="Delivery Outcomes"
      method={{
        formula: "Trips by recorded delivery status for this partner.",
        plot: "donut",
      }} icon={Target} explain="Split of delivery status across trips.">
          {delivery.length ? <DonutChart data={delivery} height={280} /> : <Empty />}
        </ChartCard>
      </div>

      {/* Top routes + drivers */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-6">
        <ChartCard title="Top Routes"
      method={{
        formula: "This partner's lanes ranked by trip count.",
        plot: "table",
      }} icon={RouteIcon} explain="Click a lane to open its route profile.">
          <MiniTable
            head={['Route', 'Trips', 'OTD', 'Avg km']}
            rows={detail.top_routes.map(r => ({
              onClick: () => navigate(`/routes/${encodeURIComponent(r.origin)}/${encodeURIComponent(r.destination)}`),
              cells: [
                <span className="text-gray-200">{r.origin} <span className="text-gray-600">→</span> {r.destination}</span>,
                r.trips,
                <span className={otdClass(r.otd_pct)}>{formatPercent(r.otd_pct)}</span>,
                formatNumber(r.avg_km),
              ],
            }))} />
        </ChartCard>
        <ChartCard title="Top Drivers"
      method={{
        formula: "Drivers who ran this partner's trips, ranked by volume.",
        plot: "table",
      }} icon={Users} explain="Click a driver to open their profile.">
          <MiniTable
            head={['Driver', 'Trips', 'OTD', 'Speed']}
            rows={detail.top_drivers.map(d => ({
              onClick: () => navigate(`/drivers/${d.driver_id}`),
              cells: [
                <span className="text-gray-200">{d.driver_name || '—'}</span>,
                d.trips,
                <span className={otdClass(d.otd_pct)}>{formatPercent(d.otd_pct)}</span>,
                formatSpeed(d.avg_speed),
              ],
            }))} />
        </ChartCard>
      </div>

      {/* Top vehicles */}
      <ChartCard title="Top Vehicles"
      method={{
        formula: "Vehicles used on this partner's trips, ranked by volume.",
        plot: "table",
      }} icon={Truck} className="mb-6" explain="Click a vehicle to open its profile.">
        <MiniTable
          head={['Vehicle', 'Type', 'Trips', 'OTD', 'Distance']}
          rows={detail.top_vehicles.map(v => ({
            onClick: () => navigate(`/vehicles/${v.vehicle_id}`),
            cells: [
              <span className="text-gray-200 font-medium">{v.asset_id}</span>,
              <span className="text-gray-500">{v.asset_type || '—'}</span>,
              v.trips,
              <span className={otdClass(v.otd_pct)}>{formatPercent(v.otd_pct)}</span>,
              formatDistance(v.total_km),
            ],
          }))} />
      </ChartCard>

      {/* Recent trips */}
      <ChartCard title="Recent Trips"
      method={{
        formula: "The most recent trips for this partner inside the filter.",
        plot: "table",
      }} icon={MapPin} explain="Click a trip to open its GPS analysis.">
        <MiniTable
          head={['Trip #', 'Route', 'Driver', 'Vehicle', 'Start', 'ETA', 'Speed', 'Distance']}
          rows={detail.recent_trips.map(t => ({
            onClick: () => navigate(`/trips/${t.dispatch_entry_no}`),
            cells: [
              <span className="text-blue-400 font-medium">{t.dispatch_entry_no}</span>,
              <span className="text-gray-300">{t.origin} <span className="text-gray-600">→</span> {t.destination}</span>,
              <span className="text-gray-300">{t.driver_name || '—'}</span>,
              <span className="text-gray-300">{t.asset_id || '—'}</span>,
              <span className="text-gray-400">{formatDateTime(t.trip_start)}</span>,
              <Badge label={t.eta_met ? 'Yes' : 'No'} variant={t.eta_met ? 'success' : 'danger'} />,
              formatSpeed(t.avg_speed_kmph),
              formatDistance(t.trip_km),
            ],
          }))} />
      </ChartCard>
    </>
  );
}

function MiniStat({ icon: Icon, label, value }: { icon: any; label: string; value: string }) {
  return (
    <div className="bg-gray-900 rounded-xl border border-gray-800 p-4 flex items-center gap-3">
      <Icon className="w-5 h-5 text-gray-500" />
      <div>
        <p className="text-lg font-bold text-white leading-none">{value}</p>
        <p className="text-xs text-gray-500 mt-1">{label}</p>
      </div>
    </div>
  );
}

function Empty() {
  return <div className="h-[280px] flex items-center justify-center text-sm text-gray-500">No data</div>;
}

interface Row { cells: React.ReactNode[]; onClick?: () => void }
function MiniTable({ head, rows }: { head: string[]; rows: Row[] }) {
  if (!rows.length) return <p className="text-sm text-gray-500 py-6 text-center">No data</p>;
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-gray-800">
            {head.map(h => <th key={h} className="px-3 py-2 text-left text-xs text-gray-500 uppercase tracking-wide">{h}</th>)}
            <th className="w-6" />
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i} onClick={r.onClick}
              className={`border-b border-gray-800/50 ${r.onClick ? 'cursor-pointer hover:bg-gray-800/50' : ''} transition-colors group`}>
              {r.cells.map((c, j) => <td key={j} className="px-3 py-2 text-gray-300">{c}</td>)}
              <td className="px-1 text-gray-600">{r.onClick && <ChevronRight className="w-3.5 h-3.5 opacity-0 group-hover:opacity-100" />}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
