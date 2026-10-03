import { useMemo } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { AlertTriangle, BarChart3, Building2, Factory, MapPinned, Route, Timer, Truck } from 'lucide-react';
import { api } from '../lib/api';
import { useApi } from '../hooks/useApi';
import { Card, DataTable, ErrorBox, KPI, PageHeader, Spinner } from '../components/ui';
import { KPIGrid } from '../components/drill';
import { TripsCard } from '../components/entity';
import { BarList, PALETTE, SeriesChart } from '../components/charts';
import { fmtDuration, fmtInt, fmtNum } from '../lib/format';

/** Hours into histogram buckets that suit detention and transit alike. */
function histogram(values: number[]) {
  const edges = [0, 1, 2, 4, 8, 12, 24, 48, 72, Infinity];
  const labels = ['<1h', '1-2h', '2-4h', '4-8h', '8-12h', '12-24h', '1-2d', '2-3d', '>3d'];
  const counts = labels.map(() => 0);
  for (const v of values) {
    const i = edges.findIndex((e, k) => v >= e && v < edges[k + 1]);
    if (i >= 0) counts[i]++;
  }
  return labels.map((l, i) => ({ bucket: l, trips: counts[i] }));
}

export default function LaneDetail() {
  const [params] = useSearchParams();
  const origin = params.get('origin') || '';
  const destination = params.get('destination') || '';
  const navigate = useNavigate();
  const lane = useApi(() => api.lane(origin, destination), [origin, destination]);

  const dists = useMemo(() => {
    const d = lane.data?.distributions;
    if (!d) return [];
    const o = histogram(d.origin_dwell_h), t = histogram(d.transit_h), x = histogram(d.dest_dwell_h);
    return o.map((row, i) => ({ bucket: row.bucket, origin: row.trips, transit: t[i].trips, destination: x[i].trips }));
  }, [lane.data]);

  if (lane.loading && !lane.data) return <Spinner label="Loading lane" />;
  if (lane.error) return <ErrorBox error={lane.error} onRetry={lane.reload} />;
  if (!lane.data) return null;
  const d = lane.data;
  const s = d.summary;
  const ctx = { origin, destination };

  return (
    <div className="animate-fade-in">
      <PageHeader back={{ to: '/geo/lanes', label: 'All lanes' }} title={<>{origin} <span className="text-gray-600">→</span> {destination}</>}
        subtitle={`${fmtInt(s.trips)} trips · ${fmtInt(s.vehicles)} vehicles · ${fmtInt(s.transporters)} transporters`} />

      <KPIGrid className="grid grid-cols-2 md:grid-cols-3 2xl:grid-cols-6 gap-3 mb-6">
        <KPI label="Trips" value={fmtInt(s.trips)} icon={MapPinned} color="blue" 
          drill={{ dataset: 'trips', params: ctx, groups: ['lane', 'quality', 'day', 'transporter'], sort: 'start' }} />
        <KPI label="Median at origin" value={fmtDuration(s.origin_dwell_p50_s)} icon={Factory} color="amber" hint={`90%: ${fmtDuration(s.origin_dwell_p90_s)}`} 
          drill={{ dataset: 'legs', params: { ...ctx, via: 'trips', metric: 'origin' }, groups: ['transporter', 'site', 'vehicle', 'lane'], sort: 'duration', note: 'one sample per physical departure or arrival' }} />
        <KPI label="Median transit" value={fmtDuration(s.transit_p50_s)} icon={Route} color="cyan" 
          drill={{ dataset: 'legs', params: { ...ctx, via: 'trips', metric: 'transit' }, groups: ['transporter', 'site', 'vehicle', 'lane'], sort: 'duration', note: 'one sample per physical departure or arrival' }} />
        <KPI label="Median at destination" value={fmtDuration(s.dest_dwell_p50_s)} icon={Timer} color="purple" 
          drill={{ dataset: 'legs', params: { ...ctx, via: 'trips', metric: 'dest' }, groups: ['transporter', 'site', 'vehicle', 'lane'], sort: 'duration', note: 'one sample per physical departure or arrival' }} />
        <KPI label="Vehicles" value={fmtInt(s.vehicles)} icon={Truck} color="green" 
          drill={{ dataset: 'trips', params: { ...ctx, measure: 'vehicles' }, groups: ['vehicle', 'lane'], sort: 'start' }} />
        <KPI label="Alerts / 100 trips" value={fmtNum(s.violations_per_100_trips)} icon={AlertTriangle} color={s.violations ? 'red' : 'gray'} 
          drill={{ dataset: 'alerts', params: { ...ctx, via: 'trips' }, groups: ['kind', 'site', 'vehicle', 'day'], sort: 'time' }} />
      </KPIGrid>

      <div className="grid grid-cols-1 xl:grid-cols-3 gap-6 mb-6">
        <Card title="How long each stage takes" icon={BarChart3} className="xl:col-span-2"
          subtitle="Trips by time at the first place, in transit, and at the last place.">
          <SeriesChart data={dists} x="bucket" height={240}
            series={[
              { key: 'origin', label: 'At origin', color: PALETTE.amber },
              { key: 'transit', label: 'Transit', color: PALETTE.cyan },
              { key: 'destination', label: 'At destination', color: PALETTE.purple },
            ]} />
        </Card>
        <Card title="Where trips start and end" icon={Factory}>
          <p className="text-xs text-gray-400 mb-2">First place seen</p>
          <BarList rows={d.first_places.map((p: any) => ({ key: p.name, label: p.name, value: p.trips }))} />
          <p className="text-xs text-gray-400 mb-2 mt-4">Last place seen</p>
          <BarList rows={d.last_places.map((p: any) => ({ key: p.name, label: p.name, value: p.trips }))} />
        </Card>
      </div>

      <Card title="Carriers on this lane" icon={Building2} className="mb-6"
        subtitle="The same lane, carrier by carrier — a like-for-like comparison.">
        <DataTable dense rows={d.transporters} rowKey={r => r.transporter}
          onRowClick={r => navigate(`/geo/transporters/detail?name=${encodeURIComponent(r.transporter)}`)}
          columns={[
            { key: 'transporter', label: 'Transporter', render: r => <span className="text-gray-100">{r.transporter}</span> },
            { key: 'trips', label: 'Trips', align: 'right', render: r => fmtInt(r.trips) },
            { key: 'origin_dwell_p50_s', label: 'At origin', align: 'right', render: r => fmtDuration(r.origin_dwell_p50_s) },
            { key: 'exit_after_gate_out_p50_s', label: 'Left after gate-out', align: 'right', render: r => fmtDuration(r.exit_after_gate_out_p50_s) },
            { key: 'transit_p50_s', label: 'Transit', align: 'right', render: r => fmtDuration(r.transit_p50_s) },
            { key: 'dest_dwell_p50_s', label: 'At destination', align: 'right', render: r => fmtDuration(r.dest_dwell_p50_s) },
            { key: 'violations_per_100_trips', label: 'Alerts /100', align: 'right', render: r => fmtNum(r.violations_per_100_trips) },
          ]} />
      </Card>

      <TripsCard ctx={ctx} total={s.trips} hide={['lane']} />
    </div>
  );
}
