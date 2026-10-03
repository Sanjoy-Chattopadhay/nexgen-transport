import { useParams, Link, Navigate } from 'react-router-dom';
import { ArrowLeft, MapPin, Clock, Gauge, AlertTriangle, Satellite, Route, BarChart3 } from 'lucide-react';
import PageContainer from '../components/layout/PageContainer';
import KPICard from '../components/ui/KPICard';
import Badge from '../components/ui/Badge';
import Spinner from '../components/ui/Spinner';
import DataTable from '../components/ui/DataTable';
import TrackPreview from '../components/tta/TrackPreview';
import PlantDelaySection from '../components/tta/PlantDelaySection';
import TripBreaks from '../components/tta/TripBreaks';
import DeliveryGeofenceVerdict from '../components/tta/DeliveryGeofenceVerdict';
import { useApi } from '../hooks/useApi';
import { getTTATrip, getTTATripGps } from '../services/tta';
import { formatNumber, formatDateTime, formatDuration, formatDistance, formatPercent } from '../lib/formatters';
import type { TTAGpsPoint } from '../types/tta';

function Field({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="bg-gray-800/50 rounded-lg p-3">
      <p className="text-xs text-gray-500 mb-1">{label}</p>
      <p className="text-sm text-gray-200 break-words">{value ?? '-'}</p>
    </div>
  );
}

export default function TTATripDetail() {
  const { tripNo } = useParams<{ tripNo: string }>();
  const { data, loading, error } = useApi(() => getTTATrip(tripNo!), [tripNo]);
  const { data: gps, loading: gpsLoading } = useApi(() => getTTATripGps(tripNo!, 1500), [tripNo]);

  if (loading) return <PageContainer title={`Trip ${tripNo}`}><Spinner /></PageContainer>;
  if (error || !data) return (
    <PageContainer title={`Trip ${tripNo}`}>
      <p className="text-red-400">{error || 'Trip not found'}</p>
    </PageContainer>
  );

  const { trip, metrics, gps_summary } = data;

  // Legacy trip-id links resolve on the backend — canonicalise the URL so
  // GPS/analysis/weather fetches use the real TTA trip number.
  if (String(trip.i_trip_no) !== String(tripNo)) {
    return <Navigate to={`/trips/${trip.i_trip_no}`} replace />;
  }

  const num = (v: unknown) => (v == null ? null : Number(v));

  const gpsColumns = [
    { key: 'dt_message', label: 'Time', render: (p: TTAGpsPoint) => formatDateTime(p.dt_message) },
    { key: 'd_lat', label: 'Lat', render: (p: TTAGpsPoint) => p.d_lat.toFixed(6) },
    { key: 'd_long', label: 'Long', render: (p: TTAGpsPoint) => p.d_long.toFixed(6) },
    { key: 's_status', label: 'Status', render: (p: TTAGpsPoint) => (
      <Badge label={p.s_status ?? '-'} variant={p.is_moving ? 'info' : 'warning'} />
    ) },
    { key: 's_wpnt1', label: 'Near Waypoint', render: (p: TTAGpsPoint) => (
      <span className="text-gray-400 text-xs">{p.s_wpnt1 ?? '-'}{p.s_wpnt1_st_abbr ? ` (${p.s_wpnt1_st_abbr})` : ''}</span>
    ) },
    { key: 'i_cdist', label: 'Cum. Dist', render: (p: TTAGpsPoint) => formatDistance(p.i_cdist / 1000) },
  ];

  return (
    <PageContainer title={`Trip ${trip.i_trip_no}`}>
      <div className="flex items-center justify-between mb-4">
        <Link to="/trips" className="inline-flex items-center gap-1.5 text-sm text-gray-400 hover:text-gray-200">
          <ArrowLeft className="w-4 h-4" /> Back to Trips
        </Link>
        <Link to={`/trips/${trip.i_trip_no}/analysis`}
          className="inline-flex items-center gap-2 px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-lg text-sm font-medium transition-colors">
          <BarChart3 className="w-4 h-4" /> Full Analysis Report
        </Link>
      </div>

      {/* KPI row */}
      <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-4 mb-6">
        <KPICard label="Distance" value={formatDistance(num(metrics?.d_distance_travelled_km))} icon={Route} color="blue" />
        <KPICard label="Transit Time" value={formatDuration(metrics?.i_transit_time_min)} icon={Clock} color="purple" />
        <KPICard label="Moving Time" value={formatDuration(metrics?.i_moving_time_min)} icon={Gauge} color="green" />
        <KPICard label="Stoppage" value={formatDuration(metrics?.i_stoppage_time_min)} icon={Clock} color="amber" />
        <KPICard label="Speed Violations" value={formatNumber(metrics?.i_speed_violation)} icon={AlertTriangle} color="red" />
        <KPICard label="GPS Pings" value={formatNumber(gps_summary?.total_pings)} icon={Satellite} color="blue" />
      </div>

      {/* In-plant delay analysis (GPS-reconstructed) */}
      <PlantDelaySection tripNo={trip.i_trip_no} />

      {/* Trip info */}
      <div className="bg-gray-900 rounded-xl border border-gray-800 p-5 mb-6">
        <div className="flex items-center justify-between mb-4 flex-wrap gap-2">
          <h2 className="text-lg font-semibold text-white flex items-center gap-2">
            <MapPin className="w-5 h-5 text-blue-400" />
            {String(trip.s_org_node_name ?? '?')} <span className="text-gray-600">→</span> {String(trip.s_dest_node_name ?? '?')}
          </h2>
          <div className="flex items-center gap-2">
            {trip.c_trip_status != null && <Badge label={String(trip.c_trip_status)} variant="success" />}
            {metrics?.s_delivery_status && <Badge label={metrics.s_delivery_status} variant={metrics.s_delivery_status.toLowerCase().includes('on time') ? 'success' : 'danger'} />}
          </div>
        </div>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          <Field label="Vehicle" value={String(trip.s_asset_id ?? '-')} />
          <Field label="Asset Type" value={String(trip.s_asset_type ?? '-')} />
          <Field label="Device ID" value={String(trip.s_device_id ?? '-')} />
          <Field label="Consignor" value={trip.s_cnr_name ? `${trip.s_cnr_name} (#${trip.i_cnr_id})` : '-'} />
          <Field label="Consignee" value={String(trip.s_cne_name ?? '-')} />
          <Field label="Transporter" value={String(trip.s_trans_name ?? '-')} />
          <Field label="Driver" value={trip.s_driver_name ? `${trip.s_driver_name} (${trip.s_driver_mobile_no ?? '-'})` : '-'} />
          <Field label="Invoice" value={String(trip.s_invoice ?? '-')} />
          <Field label="Booking" value={formatDateTime(trip.dt_booking as string)} />
          <Field label="Trip Start" value={formatDateTime(trip.dt_trip_start)} />
          <Field label="ETA" value={formatDateTime(trip.dt_trip_eta)} />
          <Field label="ATA" value={formatDateTime(trip.dt_trip_ata)} />
          <Field label="ATA Out" value={formatDateTime(metrics?.dt_ata_out)} />
          <Field label="Trip End" value={formatDateTime(trip.dt_trip_end)} />
          <Field label="Close Reason" value={String(trip.s_close_reason ?? '-')} />
          <Field label="Delivery" value={metrics?.s_delivery_dur ?? '-'} />
          <Field label="Detention" value={metrics?.s_detention ? `${metrics.s_detention} (${formatDuration(metrics.i_detention_min)})` : '-'} />
          <Field label="Plant Vivo" value={metrics?.s_plant_vivo ?? '-'} />
          <Field label="Device Uptime" value={formatPercent(num(metrics?.d_uptime_pct))} />
          <Field label="Shipment / LR No" value={String(trip.s_shipment_id ?? '-')} />
        </div>
      </div>

      {/* GPS summary + track */}
      <div className="bg-gray-900 rounded-xl border border-gray-800 p-5 mb-6">
        <h2 className="text-lg font-semibold text-white mb-4 flex items-center gap-2">
          <Satellite className="w-5 h-5 text-blue-400" /> GPS Track
          {gps && <span className="text-sm text-gray-500 font-normal">({formatNumber(gps.returned)} of {formatNumber(gps.total)} points shown)</span>}
        </h2>
        {gps_summary && gps_summary.total_pings > 0 && (
          <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 gap-3 mb-4">
            <Field label="First Ping" value={formatDateTime(gps_summary.first_ping)} />
            <Field label="Last Ping" value={formatDateTime(gps_summary.last_ping)} />
            <Field label="Moving Pings" value={`${formatNumber(num(gps_summary.moving_pings))} / ${formatNumber(gps_summary.total_pings)}`} />
            <Field label="GPS Distance" value={formatDistance(gps_summary.max_cdist_m != null ? Number(gps_summary.max_cdist_m) / 1000 : null)} />
            <Field label="Max Speed" value={gps_summary.max_speed_kmph != null ? `${gps_summary.max_speed_kmph} km/h` : '-'} />
            <Field label="Avg Moving Speed" value={gps_summary.avg_moving_speed_kmph != null ? `${Number(gps_summary.avg_moving_speed_kmph).toFixed(1)} km/h` : '-'} />
          </div>
        )}
        {gpsLoading ? <Spinner /> : gps && gps.points.length > 0 ? (
          <TrackPreview points={gps.points} />
        ) : <p className="text-gray-500 text-sm">No GPS points stored for this trip</p>}
      </div>

      {/* Where the hours went.
          This replaced a 50-row dump of raw pings, which restated "the truck
          was parked at HSM CANTEEN" once per ping. The halts themselves are
          the thing worth reading; the pings are still one click away below. */}
      {/* The consignor's "customer not geo fenced" complaint, checked against
          this trip. Sits above the break register because it is a verdict on
          the trip, not a breakdown of it. */}
      <DeliveryGeofenceVerdict tripNo={trip.i_trip_no} />

      <TripBreaks tripNo={trip.i_trip_no} />

      {/* The raw trail, folded away — occasionally you do need the pings. */}
      {gps && gps.points.length > 0 && (
        <details className="bg-gray-900 rounded-xl border border-gray-800 p-5 mt-6">
          <summary className="text-sm font-medium text-gray-400 cursor-pointer hover:text-gray-200 select-none">
            Raw GPS points ({formatNumber(gps.returned)} loaded)
          </summary>
          <div className="mt-4">
            <DataTable<TTAGpsPoint> columns={gpsColumns} data={gps.points.slice(0, 50)} />
            {gps.points.length > 50 && (
              <p className="text-xs text-gray-500 mt-2">
                Showing first 50 of {formatNumber(gps.returned)} loaded points
              </p>
            )}
          </div>
        </details>
      )}
    </PageContainer>
  );
}
