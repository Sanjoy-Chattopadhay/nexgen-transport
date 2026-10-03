import { useNavigate, useSearchParams } from 'react-router-dom';
import { AlertTriangle, Clock, Factory, LogOut, MapPinned, Route, Timer, Truck } from 'lucide-react';
import { api } from '../lib/api';
import { useApi } from '../hooks/useApi';
import { Card, DataTable, ErrorBox, KPI, PageHeader, Spinner } from '../components/ui';
import { KPIGrid } from '../components/drill';
import { AlertsCard, DailyCard, TopSitesCard, TripsCard } from '../components/entity';
import { fmtDuration, fmtInt, fmtNum, fmtPct, share } from '../lib/format';

export default function TransporterDetail() {
  const [params] = useSearchParams();
  const name = params.get('name') || '';
  const navigate = useNavigate();
  const t = useApi(() => api.transporter(name), [name]);

  if (t.loading && !t.data) return <Spinner label={`Loading ${name}`} />;
  if (t.error) return <ErrorBox error={t.error} onRetry={t.reload} />;
  if (!t.data) return null;
  const d = t.data;
  const s = d.summary;
  const ctx = { transporter: name };

  return (
    <div className="animate-fade-in">
      <PageHeader back={{ to: '/geo/transporters', label: 'All transporters' }} title={d.transporter}
        subtitle={`${fmtInt(s.vehicles)} vehicles · ${fmtInt(s.drivers)} drivers · ${fmtInt(s.trips)} trips`} />

      <KPIGrid className="grid grid-cols-2 md:grid-cols-4 2xl:grid-cols-8 gap-3 mb-6">
        <KPI label="Trips" value={fmtInt(s.trips)} icon={MapPinned} color="blue" 
          drill={{ dataset: 'trips', params: ctx, groups: ['lane', 'quality', 'day', 'transporter'], sort: 'start' }} />
        <KPI label="Vehicles" value={fmtInt(s.vehicles)} icon={Truck} color="green" 
          drill={{ dataset: 'trips', params: { ...ctx, measure: 'vehicles' }, groups: ['vehicle', 'lane'], sort: 'start' }} />
        <KPI label="Median at loading place" value={fmtDuration(s.origin_dwell_p50_s)} icon={Factory} color="amber"
          hint={`90%: ${fmtDuration(s.origin_dwell_p90_s)}`} 
          drill={{ dataset: 'legs', params: { ...ctx, via: 'trips', metric: 'origin' }, groups: ['transporter', 'site', 'vehicle', 'lane'], sort: 'duration', note: 'one sample per physical departure or arrival' }} />
        <KPI label="Left after gate-out" value={fmtDuration(s.exit_after_gate_out_p50_s)} icon={LogOut} color="amber" hint="median" 
          drill={{ dataset: 'legs', params: { ...ctx, via: 'trips', metric: 'gateout' }, groups: ['transporter', 'site', 'vehicle', 'lane'], sort: 'duration', note: 'one sample per physical departure or arrival' }} />
        <KPI label="Median transit" value={fmtDuration(s.transit_p50_s)} icon={Route} color="cyan" 
          drill={{ dataset: 'legs', params: { ...ctx, via: 'trips', metric: 'transit' }, groups: ['transporter', 'site', 'vehicle', 'lane'], sort: 'duration', note: 'one sample per physical departure or arrival' }} />
        <KPI label="Median at destination" value={fmtDuration(s.dest_dwell_p50_s)} icon={Timer} color="purple" 
          drill={{ dataset: 'legs', params: { ...ctx, via: 'trips', metric: 'dest' }, groups: ['transporter', 'site', 'vehicle', 'lane'], sort: 'duration', note: 'one sample per physical departure or arrival' }} />
        <KPI label="Alerts / 100 trips" value={fmtNum(s.violations_per_100_trips)} icon={AlertTriangle} color={s.violations ? 'red' : 'gray'} 
          drill={{ dataset: 'alerts', params: { ...ctx, via: 'trips' }, groups: ['kind', 'site', 'vehicle', 'day'], sort: 'time' }} />
        <KPI label="Trips with broken GPS" value={fmtPct(share(s.broken_trips, s.trips), 0)} icon={Clock} color="gray" 
          drill={{ dataset: 'trips', params: { ...ctx, preset: 'broken' }, groups: ['vehicle', 'day'], sort: 'moving_gaps' }} />
      </KPIGrid>

      <div className="grid grid-cols-1 xl:grid-cols-2 gap-6 mb-6">
        <DailyCard daily={d.daily} />
        <TopSitesCard sites={d.top_sites} />
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-2 gap-6 mb-6">
        <Card title="Vehicles" icon={Truck}>
          <div className="max-h-[380px] overflow-y-auto">
            <DataTable dense rows={d.vehicles} rowKey={r => r.s_asset_id}
              onRowClick={r => navigate(`/geo/vehicles/${encodeURIComponent(r.s_asset_id)}`)}
              columns={[
                { key: 's_asset_id', label: 'Vehicle', render: r => <span className="text-gray-100">{r.s_asset_id}</span> },
                { key: 'trips', label: 'Trips', align: 'right', render: r => fmtInt(r.trips) },
                { key: 'origin_dwell_p50_s', label: 'At loading', align: 'right', render: r => fmtDuration(r.origin_dwell_p50_s) },
                { key: 'transit_p50_s', label: 'Transit', align: 'right', render: r => fmtDuration(r.transit_p50_s) },
                { key: 'violations', label: 'Alerts', align: 'right', render: r => r.violations ? <span className="text-red-400">{r.violations}</span> : '0' },
              ]} />
          </div>
        </Card>
        <Card title="Lanes" icon={Route}>
          <div className="max-h-[380px] overflow-y-auto">
            <DataTable dense rows={d.lanes}
              onRowClick={r => navigate(`/geo/lanes/detail?origin=${encodeURIComponent(r.origin || '?')}&destination=${encodeURIComponent(r.destination || '?')}`)}
              columns={[
                { key: 'lane', label: 'Lane', render: r => <span className="text-gray-100 text-xs">{r.origin || '?'} → {r.destination || '?'}</span> },
                { key: 'trips', label: 'Trips', align: 'right', render: r => fmtInt(r.trips) },
                { key: 'origin_dwell_p50_s', label: 'At loading', align: 'right', render: r => fmtDuration(r.origin_dwell_p50_s) },
                { key: 'transit_p50_s', label: 'Transit', align: 'right', render: r => fmtDuration(r.transit_p50_s) },
              ]} />
          </div>
        </Card>
      </div>

      <div className="mb-6"><AlertsCard ctx={ctx} /></div>
      <TripsCard ctx={ctx} total={s.trips} hide={['s_trans_name']} />
    </div>
  );
}
