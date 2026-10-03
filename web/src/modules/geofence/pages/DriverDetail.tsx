import { useNavigate, useSearchParams } from 'react-router-dom';
import { AlertTriangle, Factory, MapPinned, Route, Truck } from 'lucide-react';
import { api } from '../lib/api';
import { useApi } from '../hooks/useApi';
import { Card, ErrorBox, KPI, PageHeader, Spinner } from '../components/ui';
import { KPIGrid } from '../components/drill';
import { AlertsCard, TopSitesCard, TripsCard } from '../components/entity';
import { BarList } from '../components/charts';
import { fmtDuration, fmtInt, fmtNum } from '../lib/format';

export default function DriverDetail() {
  const [params] = useSearchParams();
  const name = params.get('name') || '';
  const navigate = useNavigate();
  const drv = useApi(() => api.driver(name), [name]);

  if (drv.loading && !drv.data) return <Spinner label={`Loading ${name}`} />;
  if (drv.error) return <ErrorBox error={drv.error} onRetry={drv.reload} />;
  if (!drv.data) return null;
  const d = drv.data;
  const s = d.summary;
  const ctx = { driver: name };

  return (
    <div className="animate-fade-in">
      <PageHeader back={{ to: '/geo/drivers', label: 'All drivers' }} title={d.driver} subtitle={d.mobile || undefined} />
      <KPIGrid className="grid grid-cols-2 md:grid-cols-5 gap-3 mb-6">
        <KPI label="Trips" value={fmtInt(s.trips)} icon={MapPinned} color="blue" 
          drill={{ dataset: 'trips', params: ctx, groups: ['lane', 'quality', 'day', 'transporter'], sort: 'start' }} />
        <KPI label="Vehicles driven" value={fmtInt(s.vehicles)} icon={Truck} color="green" 
          drill={{ dataset: 'trips', params: { ...ctx, measure: 'vehicles' }, groups: ['vehicle', 'lane'], sort: 'start' }} />
        <KPI label="Median at loading place" value={fmtDuration(s.origin_dwell_p50_s)} icon={Factory} color="amber" 
          drill={{ dataset: 'legs', params: { ...ctx, via: 'trips', metric: 'origin' }, groups: ['transporter', 'site', 'vehicle', 'lane'], sort: 'duration', note: 'one sample per physical departure or arrival' }} />
        <KPI label="Median transit" value={fmtDuration(s.transit_p50_s)} icon={Route} color="cyan" 
          drill={{ dataset: 'legs', params: { ...ctx, via: 'trips', metric: 'transit' }, groups: ['transporter', 'site', 'vehicle', 'lane'], sort: 'duration', note: 'one sample per physical departure or arrival' }} />
        <KPI label="Alerts / 100 trips" value={fmtNum(s.violations_per_100_trips)} icon={AlertTriangle} color={s.violations ? 'red' : 'gray'} 
          drill={{ dataset: 'alerts', params: { ...ctx, via: 'trips' }, groups: ['kind', 'site', 'vehicle', 'day'], sort: 'time' }} />
      </KPIGrid>
      <div className="grid grid-cols-1 xl:grid-cols-3 gap-6 mb-6">
        <div className="xl:col-span-2"><AlertsCard ctx={ctx} /></div>
        <Card title="Vehicles" icon={Truck}>
          <BarList rows={d.vehicles.map((v: any) => ({
            key: v.s_asset_id, label: v.s_asset_id, value: v.trips,
            onClick: () => navigate(`/geo/vehicles/${encodeURIComponent(v.s_asset_id)}`),
          }))} />
        </Card>
      </div>
      <div className="mb-6"><TopSitesCard sites={d.top_sites} /></div>
      <TripsCard ctx={ctx} total={s.trips} hide={['s_driver_name']} />
    </div>
  );
}
