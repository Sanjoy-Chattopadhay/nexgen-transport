import { Fragment, useState } from 'react';
import { Flame, MapPin, Radar, Landmark, Database } from 'lucide-react';
import PageContainer from '../components/layout/PageContainer';
import Spinner from '../components/ui/Spinner';
import DataTable from '../components/ui/DataTable';
import KPICard from '../components/ui/KPICard';
import BarChart from '../components/charts/BarChart';
import HeatLayerMap from '../components/tta/HeatLayerMap';
import { useApi } from '../hooks/useApi';
import {
  getTTANetworkWaypoints, getTTAHeatmap, getTTAWaypointHours, getTTAStates,
  getTTAMaintenanceStatus,
} from '../services/tta';
import { formatNumber } from '../lib/formatters';
import { tc } from '../../../core/theme';

function heatColor(v: number, max: number): string {
  if (v === 0 || max === 0) return 'bg-gray-800/40';
  const i = v / max;
  if (i < 0.2) return 'bg-blue-900/50';
  if (i < 0.4) return 'bg-cyan-700/50';
  if (i < 0.6) return 'bg-emerald-600/50';
  if (i < 0.8) return 'bg-yellow-500/50';
  return 'bg-red-500/60';
}

export default function TTANetwork() {
  const [heatMode, setHeatMode] = useState<'all' | 'stops'>('all');

  const { data: wp, loading: wpLoading } = useApi(() => getTTANetworkWaypoints(25));
  const { data: heat, loading: heatLoading } = useApi(() => getTTAHeatmap(heatMode), [heatMode]);
  const { data: wpHours } = useApi(() => getTTAWaypointHours(12));
  const { data: states } = useApi(() => getTTAStates());
  const { data: maint } = useApi(() => getTTAMaintenanceStatus());

  const maxCell = wpHours ? Math.max(...(wpHours.matrix as any[]).flatMap((m: any) => m.hours), 1) : 1;

  return (
    <PageContainer>
      {/* Fleet KPIs */}
      {wp?.kpis && (
        <div className="grid grid-cols-2 md:grid-cols-5 gap-4 mb-6">
          <KPICard label="GPS Pings" value={formatNumber(wp.kpis.pings)} icon={Radar} color="blue" />
          <KPICard label="Waypoints Seen" value={formatNumber(wp.kpis.waypoints)} icon={MapPin} color="purple" />
          <KPICard label="States Covered" value={wp.kpis.states} icon={Landmark} color="cyan" />
          <KPICard label="Trips Tracked" value={formatNumber(wp.kpis.trips)} icon={Flame} color="green" />
          <KPICard label="Vehicles" value={formatNumber(wp.kpis.vehicles)} icon={Radar} color="amber" />
        </div>
      )}

      {/* India heatmap */}
      <div className="bg-gray-900 rounded-xl border border-gray-800 p-5 mb-6">
        <div className="flex items-center justify-between flex-wrap gap-3 mb-4">
          <div>
            <h2 className="text-lg font-semibold text-white flex items-center gap-2">
              <Flame className="w-5 h-5 text-orange-400" /> India Heatmap
            </h2>
            <p className="text-xs text-gray-500 mt-1">
              Fleet-wide GPS density on the map of India — switch to stop hot-zones to see where trucks stand still
            </p>
          </div>
          <div className="flex rounded-lg overflow-hidden border border-gray-700">
            {(['all', 'stops'] as const).map(m => (
              <button key={m} onClick={() => setHeatMode(m)}
                className={`px-4 py-1.5 text-xs font-medium transition-colors ${heatMode === m ? 'bg-blue-600 text-white' : 'bg-gray-800 text-gray-400 hover:text-gray-200'}`}>
                {m === 'all' ? 'All movement' : 'Stop hot-zones'}
              </button>
            ))}
          </div>
        </div>
        {heatLoading ? <Spinner /> : heat && (
          <HeatLayerMap cells={heat.cells as [number, number, number][]} height={520} />
        )}
      </div>

      {/* Waypoint dwell leaders */}
      <div className="bg-gray-900 rounded-xl border border-gray-800 p-5 mb-6">
        <h2 className="text-lg font-semibold text-white flex items-center gap-2 mb-1">
          <MapPin className="w-5 h-5 text-amber-400" /> Waypoint Analysis — Dwell Leaders
        </h2>
        <p className="text-xs text-gray-500 mb-4">Where the fleet's hours actually go — waypoints ranked by total standstill time</p>
        {wpLoading ? <Spinner /> : wp && (
          <>
            <BarChart
              data={(wp.leaders as any[]).slice(0, 12).map((l: any) => ({
                ...l, name: l.waypoint.length > 16 ? l.waypoint.slice(0, 16) + '…' : l.waypoint,
              }))}
              xKey="name"
              series={[
                { key: 'stopped_hours', color: tc('#f59e0b'), label: 'Stopped hours' },
                { key: 'moving_hours', color: tc('#3b82f6'), label: 'Moving hours' },
              ]}
              height={260}
            />
            <div className="mt-4">
              <DataTable columns={[
                { key: 'waypoint', label: 'Waypoint', render: (r: any) => <span className="text-gray-200">{r.waypoint} <span className="text-gray-500 text-xs">({r.state})</span></span> },
                { key: 'stopped_hours', label: 'Stopped Hrs', render: (r: any) => <span className="text-amber-400 font-semibold">{r.stopped_hours}</span> },
                { key: 'moving_hours', label: 'Moving Hrs' },
                { key: 'trips', label: 'Trips' },
                { key: 'vehicles', label: 'Vehicles' },
                { key: 'pings', label: 'Pings', render: (r: any) => formatNumber(r.pings) },
              ]} data={wp.leaders} />
            </div>
          </>
        )}
      </div>

      {/* Waypoint x hour heatmap */}
      <div className="bg-gray-900 rounded-xl border border-gray-800 p-5 mb-6">
        <h2 className="text-lg font-semibold text-white flex items-center gap-2 mb-1">
          <Flame className="w-5 h-5 text-red-400" /> Waypoint Heatmap — When Places Jam Up
        </h2>
        <p className="text-xs text-gray-500 mb-4">Standstill minutes per hour of day at the top dwell waypoints</p>
        {wpHours && wpHours.matrix.length > 0 ? (
          <div className="overflow-x-auto">
            <div className="inline-grid gap-0.5" style={{ gridTemplateColumns: '170px repeat(24, 1fr)' }}>
              <div />
              {Array.from({ length: 24 }, (_, h) => (
                <div key={h} className="text-center text-xs text-gray-500 pb-1 w-7">{h}</div>
              ))}
              {(wpHours.matrix as any[]).map((row: any) => (
                <Fragment key={row.waypoint}>
                  <div className="text-xs text-gray-400 flex items-center pr-2 truncate" title={row.waypoint}>
                    {row.waypoint.length > 22 ? row.waypoint.slice(0, 22) + '…' : row.waypoint}
                  </div>
                  {row.hours.map((v: number, h: number) => (
                    <div key={`${row.waypoint}-${h}`}
                      className={`w-7 h-7 rounded-sm ${heatColor(v, maxCell)} flex items-center justify-center`}
                      title={`${row.waypoint} @ ${h}:00 — ${v} min standstill`}>
                      {v > 0 && <span className="text-xs text-gray-200">{v >= 60 ? Math.round(v / 60) + 'h' : v}</span>}
                    </div>
                  ))}
                </Fragment>
              ))}
            </div>
          </div>
        ) : <p className="text-gray-500 text-sm">No waypoint data yet</p>}
      </div>

      {/* State distribution */}
      <div className="bg-gray-900 rounded-xl border border-gray-800 p-5 mb-6">
        <h2 className="text-lg font-semibold text-white flex items-center gap-2 mb-4">
          <Landmark className="w-5 h-5 text-cyan-400" /> State-wise Activity
        </h2>
        {states && (
          <DataTable columns={[
            { key: 'state', label: 'State', render: (r: any) => <span className="text-blue-400 font-semibold">{r.state}</span> },
            { key: 'pings', label: 'Pings', render: (r: any) => formatNumber(r.pings) },
            { key: 'moving_hours', label: 'Moving Hrs' },
            { key: 'stopped_hours', label: 'Stopped Hrs', render: (r: any) => <span className="text-amber-400">{r.stopped_hours}</span> },
            { key: 'trips', label: 'Trips' },
            { key: 'vehicles', label: 'Vehicles' },
          ]} data={states.states} />
        )}
      </div>

      {/* Data lifecycle */}
      <div className="bg-gray-900 rounded-xl border border-gray-800 p-5">
        <h2 className="text-lg font-semibold text-white flex items-center gap-2 mb-1">
          <Database className="w-5 h-5 text-emerald-400" /> Data Lifecycle (built for 3,000 trips/day)
        </h2>
        <p className="text-xs text-gray-500 mb-4">Auto-backup with rotation + GPS retention purge (archive first, then delete)</p>
        {maint && (
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            <div className="bg-gray-800/50 rounded-lg p-3">
              <p className="text-xs text-gray-500 mb-1">Database Size</p>
              <p className="text-lg font-bold text-gray-100">{maint.db_size_mb} MB</p>
            </div>
            <div className="bg-gray-800/50 rounded-lg p-3">
              <p className="text-xs text-gray-500 mb-1">GPS Pings Stored</p>
              <p className="text-lg font-bold text-blue-400">{formatNumber(maint.gps.pings)}</p>
              <p className="text-xs text-gray-500">{maint.gps.oldest?.slice(0, 10)} → {maint.gps.newest?.slice(0, 10)}</p>
            </div>
            <div className="bg-gray-800/50 rounded-lg p-3">
              <p className="text-xs text-gray-500 mb-1">Retention Window</p>
              <p className="text-lg font-bold text-gray-100">{maint.retention_days} days</p>
              <p className="text-xs text-gray-500">{maint.trips_awaiting_purge} trips awaiting purge</p>
            </div>
            <div className="bg-gray-800/50 rounded-lg p-3">
              <p className="text-xs text-gray-500 mb-1">Backups Kept</p>
              <p className="text-lg font-bold text-emerald-400">{maint.backups.length}</p>
              <p className="text-xs text-gray-500">{maint.backups[0]?.file ?? 'none yet'}</p>
            </div>
          </div>
        )}
      </div>
    </PageContainer>
  );
}
