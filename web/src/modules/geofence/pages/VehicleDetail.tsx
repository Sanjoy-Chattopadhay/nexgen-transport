import { useNavigate, useParams } from 'react-router-dom';
import { AlertTriangle, Building2, Clock, Factory, MapPinned, Route, Timer, Truck, UserRound } from 'lucide-react';
import { api } from '../lib/api';
import { useApi } from '../hooks/useApi';
import { Badge, Card, ErrorBox, KPI, Note, PageHeader, Spinner } from '../components/ui';
import { KPIGrid } from '../components/drill';
import { AlertsCard, DailyCard, TopSitesCard, TripsCard } from '../components/entity';
import { BarList } from '../components/charts';
import { fmtDateTime, fmtDuration, fmtInt, fmtKm } from '../lib/format';

export default function VehicleDetail() {
  const { assetId = '' } = useParams();
  const navigate = useNavigate();
  const v = useApi(() => api.vehicle(assetId), [assetId]);

  if (v.loading && !v.data) return <Spinner label={`Loading ${assetId}`} />;
  if (v.error) return <ErrorBox error={v.error} onRetry={v.reload} />;
  if (!v.data) return null;
  const d = v.data;
  const s = d.summary;
  const ctx = { vehicle: assetId };

  return (
    <div className="animate-fade-in">
      <PageHeader back={{ to: '/geo/vehicles', label: 'All vehicles' }} title={d.s_asset_id}
        badges={<>{d.s_asset_type && <Badge variant="info">{d.s_asset_type}</Badge>}
          {d.live && <Badge variant="success">live</Badge>}</>}
        subtitle={d.transporters.map((t: any) => t.name).join(', ')} />

      {d.live && (
        <div className="mb-6">
          <Note tone="good" title="Live:">
            last fix {fmtDateTime(d.live.dt_message)} · {d.live.i_speed ?? 0} km/h ·{' '}
            {d.live.s_site_name ? `inside ${d.live.s_site_name}` : 'not inside a facility'}
          </Note>
        </div>
      )}

      <KPIGrid className="grid grid-cols-2 md:grid-cols-4 2xl:grid-cols-8 gap-3 mb-6">
        <KPI label="Trips" value={fmtInt(s.trips)} icon={MapPinned} color="blue" 
          drill={{ dataset: 'trips', params: ctx, groups: ['lane', 'quality', 'day', 'transporter'], sort: 'start' }} />
        <KPI label="Facility visits" value={fmtInt(s.facility_visits)} icon={Factory} color="cyan" 
          drill={{ dataset: 'visits', params: { ...ctx, via: 'trips', preset: 'primary_facility' }, groups: ['site', 'day', 'hour'], sort: 'enter', note: 'each stay once' }} />
        <KPI label="Time at facilities" value={fmtDuration(s.facility_dwell_s)} icon={Clock} color="purple" 
          drill={{ dataset: 'places', params: { ...ctx, via: 'trips' }, groups: ['site', 'hour'], sort: 'duration', note: 'nested fences unioned, each stay once' }} />
        <KPI label="Median at loading place" value={fmtDuration(s.origin_dwell_p50_s)} icon={Timer} color="amber" 
          drill={{ dataset: 'legs', params: { ...ctx, via: 'trips', metric: 'origin' }, groups: ['transporter', 'site', 'vehicle', 'lane'], sort: 'duration', note: 'one sample per physical departure or arrival' }} />
        <KPI label="Median transit" value={fmtDuration(s.transit_p50_s)} icon={Route} color="green" 
          drill={{ dataset: 'legs', params: { ...ctx, via: 'trips', metric: 'transit' }, groups: ['transporter', 'site', 'vehicle', 'lane'], sort: 'duration', note: 'one sample per physical departure or arrival' }} />
        <KPI label="Distance" value={fmtKm(s.distance_km)} icon={Truck} color="blue" 
          drill={{ dataset: 'trips', params: { ...ctx, measure: 'km' }, groups: ['lane', 'day'], sort: 'km', note: 'each kilometre once, however many consignments rode it' }} />
        <KPI label="Alerts" value={fmtInt(s.violations)} icon={AlertTriangle} color={s.violations ? 'red' : 'gray'} 
          drill={{ dataset: 'alerts', params: { ...ctx, via: 'trips' }, groups: ['kind', 'site', 'vehicle', 'day'], sort: 'time' }} />
        <KPI label="Trips with broken GPS" value={fmtInt(s.broken_trips)} icon={AlertTriangle} color="amber" 
          drill={{ dataset: 'trips', params: { ...ctx, preset: 'broken' }, groups: ['vehicle', 'day'], sort: 'moving_gaps' }} />
      </KPIGrid>

      <div className="grid grid-cols-1 xl:grid-cols-3 gap-6 mb-6">
        <div className="xl:col-span-2"><DailyCard daily={d.daily} /></div>
        <Card title="Drivers and carriers" icon={UserRound}>
          <p className="text-xs text-gray-400 mb-2">Drivers</p>
          <BarList rows={d.drivers.map((x: any) => ({
            key: x.name, label: x.name, value: x.trips,
            onClick: () => navigate(`/geo/drivers/detail?name=${encodeURIComponent(x.name)}`),
          }))} />
          <p className="text-xs text-gray-400 mb-2 mt-4 flex items-center gap-1"><Building2 className="w-3.5 h-3.5" /> Transporters</p>
          <BarList rows={d.transporters.map((x: any) => ({
            key: x.name, label: x.name, value: x.trips,
            onClick: () => navigate(`/geo/transporters/detail?name=${encodeURIComponent(x.name)}`),
          }))} />
        </Card>
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-2 gap-6 mb-6">
        <TopSitesCard sites={d.top_sites} />
        <AlertsCard ctx={ctx} />
      </div>

      <TripsCard ctx={ctx} total={s.trips} hide={['asset']} />
    </div>
  );
}
