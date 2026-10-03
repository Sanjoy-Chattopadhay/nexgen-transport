import { useMemo } from 'react';
import { Home, Truck, Satellite, Factory, AlertTriangle, WifiOff, BarChart3 } from 'lucide-react';
import PageContainer from '../../components/layout/PageContainer';
import Spinner from '../../components/ui/Spinner';
import ChartCard from '../../components/ui/ChartCard';
import DataTable from '../../components/ui/DataTable';
import BarChart from '../../components/charts/BarChart';
import DonutChart from '../../components/charts/DonutChart';
import HBarChart from '../../components/charts/HBarChart';
import HistogramChart from '../../components/charts/HistogramChart';
import { useApi } from '../../hooks/useApi';
import { useTTAFilters } from '../../components/tta/dashboard/FilterContext';
import { getDashDistribution, getDashFleet, type GroupRow } from '../../services/ttaDashboard';
import { formatNumber } from '../../lib/formatters';
import { tc } from '../../../../core/theme';

export default function Fleet() {
  const { params, paramsKey } = useTTAFilters();
  const { data, loading } = useApi(() => getDashFleet(params), [paramsKey]);
  const { data: uptime } = useApi(() => getDashDistribution('gps_uptime', params), [paramsKey]);

  const ownMarket: GroupRow[] = data?.own_market ?? [];

  // long-format compare: one row per KPI with a column per class
  const compare = useMemo(() => {
    const kpis: { key: keyof GroupRow; label: string }[] = [
      { key: 'otd_pct', label: 'OTD %' },
      { key: 'avg_transit_hours', label: 'Transit h' },
      { key: 'avg_detention_hours', label: 'Detention h' },
      { key: 'violations_per_trip', label: 'Alerts/trip' },
      { key: 'avg_gps_uptime', label: 'GPS %' },
    ];
    return kpis.map(k => {
      const row: Record<string, any> = { name: k.label };
      for (const om of ownMarket) row[om.name] = om[k.key];
      return row;
    });
  }, [ownMarket]);

  const omColors = [tc('#3b82f6'), tc('#f59e0b'), tc('#10b981'), tc('#ef4444')];

  return (
    <PageContainer title="🚛 Fleet & Vehicles">
      {loading ? <Spinner /> : (
        <>
          {/* Own vs Market */}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
            {ownMarket.map(om => (
              <div key={om.name} className="bg-gray-900 rounded-xl border border-gray-800 p-4">
                <p className="text-xs text-gray-500 mb-1 flex items-center gap-1.5">
                  <Home className="w-3.5 h-3.5 text-blue-400" /> {om.name} fleet
                </p>
                <p className="text-xl font-bold text-gray-100">{formatNumber(om.trips)} trips</p>
                <p className="text-xs text-gray-500 mt-1">
                  OTD {om.otd_pct ?? '—'}% · transit {om.avg_transit_hours ?? '—'} h · {om.vehicles} vehicles
                </p>
              </div>
            ))}
          </div>

          <div className="grid lg:grid-cols-2 gap-6 mb-6">
            <ChartCard title="🏠 Own vs Market"
        method={{
          formula: "Trips split by whether the truck was owned or hired, with on-time per slice.",
          plot: "donut",
        }} icon={Home} iconColor="text-blue-400"
              explain="If Own is clearly better, critical customers should ride Own; the gap is the price of market-fleet flexibility.">
              {ownMarket.length ? (
                <BarChart data={compare} xKey="name" height={280}
                  series={ownMarket.map((om, i) => ({ key: om.name, label: om.name, color: omColors[i % omColors.length] }))} />
              ) : <p className="text-gray-500 text-sm py-6">No Own/Market data</p>}
            </ChartCard>

            <ChartCard title="Trips by Vehicle Category"
        method={{
          formula: "Trips by derived vehicle class (trailer / heavy / light / spec).",
          plot: "donut",
          caveat: "The class is derived from free-text vehicle type, so ‘unspecified’ is a data-entry finding, not a vehicle kind.",
        }} icon={Truck} iconColor="text-cyan-400"
              explain="Fleet-mix fit: derived from the asset type (trailer / heavy / light / other).">
              <DonutChart height={280}
                data={(data?.vehicle_category ?? []).map((r: GroupRow) => ({ name: r.name, value: r.trips }))} />
            </ChartCard>
          </div>

          <div className="grid lg:grid-cols-2 gap-6 mb-6">
            <ChartCard title="GPS Device / Tag Type"
        method={{
          formula: "Trips by tracking device type.",
          plot: "donut",
        }} icon={Satellite} iconColor="text-purple-400"
              explain="Tracking quality by device tag — rented trackers usually mean market trucks; lower uptime = a partially invisible fleet.">
              <HBarChart data={data?.device_type ?? []} nameKey="name" valueKey="trips"
                valueLabel="Trips" colorByKey="avg_gps_uptime" colorLo={50} colorHi={100} />
            </ChartCard>
            <ChartCard title="Asset Make Performance"
        method={{
          formula: "Per manufacturer, aggregated over the filtered trips.",
          plot: "table",
        }} icon={Factory} iconColor="text-amber-400"
              explain="Only where telematics metadata exists — treat as indicative, not a purchase order.">
              {(data?.asset_make ?? []).length ? (
                <HBarChart data={data.asset_make} nameKey="name" valueKey="trips"
                  valueLabel="Trips" colorByKey="otd_pct" colorLo={50} colorHi={100} />
              ) : <p className="text-gray-500 text-sm py-6">No asset make metadata in the current selection</p>}
            </ChartCard>
          </div>

          <div className="grid lg:grid-cols-2 gap-6 mb-6">
            <ChartCard title="🚨 Top Speed-violating Vehicles"
        method={{
          formula: "Vehicles ranked by the provider's summed violation count.",
          plot: "table",
        }} icon={AlertTriangle} iconColor="text-red-400"
              explain="A handful of vehicles usually owns a huge share of alerts — share this list with the named transporters.">
              <div className="max-h-[360px] overflow-y-auto">
                <DataTable columns={[
                  { key: 'vehicle_no', label: 'Vehicle', render: (r: any) => <span className="text-gray-200 font-mono text-xs">{r.vehicle_no}</span> },
                  { key: 'trips', label: 'Trips' },
                  { key: 'violations', label: 'Alerts', render: (r: any) => <span className="text-red-400 font-semibold">{formatNumber(r.violations)}</span> },
                  { key: 'avg_gps_uptime', label: 'GPS %' },
                  { key: 'total_km', label: 'Total km' },
                  { key: 'transporter', label: 'Transporter' },
                ]} data={data?.top_violating_vehicles ?? []} />
              </div>
            </ChartCard>
            <ChartCard title="📡 Low GPS-uptime Vehicles"
        method={{
          formula: "Vehicles whose mean uptime is under 80% across at least 2 trips.",
          plot: "table",
        }} icon={WifiOff} iconColor="text-amber-400"
              explain="Vehicles under 80% uptime with ≥ 2 trips, worst first — these are your tracking blind spots.">
              <div className="max-h-[360px] overflow-y-auto">
                <DataTable columns={[
                  { key: 'vehicle_no', label: 'Vehicle', render: (r: any) => <span className="text-gray-200 font-mono text-xs">{r.vehicle_no}</span> },
                  { key: 'trips', label: 'Trips' },
                  { key: 'avg_gps_uptime', label: 'GPS %', render: (r: any) => <span className="text-amber-400 font-semibold">{r.avg_gps_uptime}</span> },
                  { key: 'total_km', label: 'Total km' },
                  { key: 'transporter', label: 'Transporter' },
                ]} data={data?.low_gps_vehicles ?? []} emptyMessage="No chronic low-uptime vehicles 🎉" />
              </div>
            </ChartCard>
          </div>

          <ChartCard title="GPS Uptime Distribution"
        method={{
          formula: "Distribution of per-trip GPS uptime percentage.",
          plot: "histogram",
          caveat: "A cluster at 100% with a separate cluster near 0% means two populations — working trackers and dead ones — not an average somewhere in between.",
        }} icon={BarChart3} iconColor="text-emerald-400"
            explain="Healthy fleets pile up near 100%; a second bump lower down = chronic tracking problems (often rentals).">
            {uptime?.histogram?.length ? (
              <HistogramChart bins={uptime.histogram} mean={uptime.stats?.mean} median={uptime.stats?.median} color={tc(tc('#10b981'))} />
            ) : <p className="text-gray-500 text-sm py-6">No GPS uptime data</p>}
          </ChartCard>
        </>
      )}
    </PageContainer>
  );
}
