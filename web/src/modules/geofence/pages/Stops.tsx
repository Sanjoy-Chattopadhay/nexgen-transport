import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import L from 'leaflet';
import { CircleDot, Clock, MapPin, Truck } from 'lucide-react';
import { api } from '../lib/api';
import { useApi } from '../hooks/useApi';
import {
  Card, DataTable, DateInput, ErrorBox, KPI, Note, PageHeader, Pagination, SearchInput, Select, Toolbar, TripRef,
  useSort,
} from '../components/ui';
import { KPIGrid } from '../components/drill';
import GeoMap from '../components/map/GeoMap';
import { PALETTE } from '../components/charts';
import { fmtDateTime, fmtDuration, fmtHours, fmtInt } from '../lib/format';

export default function Stops() {
  const navigate = useNavigate();
  const [q, setQ] = useState('');
  const [min, setMin] = useState('30');
  const [scope, setScope] = useState('outside');
  const [from, setFrom] = useState('');
  const [to, setTo] = useState('');
  const [page, setPage] = useState(1);
  const { sort, order, onSort } = useSort('duration');
  const [map, setMap] = useState<L.Map | null>(null);
  const data = useApi(() => api.stops({
    q, from, to, sort, order, page, page_size: 50, min_minutes: Number(min), outside_only: scope === 'outside',
  }), [q, min, scope, from, to, sort, order, page]);
  const d = data.data;

  useEffect(() => {
    if (!map || !d?.hotspots) return;
    const group = L.layerGroup().addTo(map);
    const top = Math.max(1, ...d.hotspots.map((h: any) => h.vehicles));
    for (const h of d.hotspots) {
      L.circleMarker([h.lat, h.lon], {
        radius: 5 + 14 * Math.sqrt(h.vehicles / top), color: PALETTE.amber, weight: 1.5, fillOpacity: 0.3,
      }).bindTooltip(`${h.vehicles} vehicles · ${h.stops} stops · ${fmtHours(h.duration_s, 0)} standing`, { className: 'geo-tip' })
        .addTo(group);
    }
    if (d.hotspots.length) {
      map.fitBounds(L.latLngBounds(d.hotspots.map((h: any) => [h.lat, h.lon])), { padding: [30, 30], maxZoom: 12 });
    }
    return () => { group.remove(); };
  }, [map, d?.hotspots]);

  const reset = (fn: (v: string) => void) => (v: string) => { fn(v); setPage(1); };
  const ctx = { q, from, to, min_minutes: Number(min), preset: scope === 'outside' ? 'outside' : undefined };

  return (
    <div className="animate-fade-in">
      <PageHeader title="Stops & hotspots"
        subtitle="Where trucks stood still. A stop inside a facility is a visit seen from the other side; a stop outside every fence is an unscheduled halt — or a place the fence master is missing." />

      <KPIGrid className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-6">
        <KPI label={scope === 'outside' ? 'Stops outside fences' : 'Stops'} value={fmtInt(d?.stops_total)} icon={CircleDot} color="amber" 
          drill={{ dataset: 'stops', params: ctx, groups: ['spot', 'vehicle', 'transporter', 'hour'], sort: 'duration' }} />
        <KPI label="Time standing" value={fmtHours(d?.duration_total_s, 0)} icon={Clock} color="purple" 
          drill={{ dataset: 'stops', params: { ...ctx, measure: 'duration' }, groups: ['transporter', 'vehicle', 'spot', 'lane'], sort: 'duration' }} />
        <KPI label="Vehicles" value={fmtInt(d?.vehicles)} icon={Truck} color="blue" 
          drill={{ dataset: 'stops', params: { ...ctx, measure: 'duration' }, groups: ['vehicle', 'transporter'], sort: 'duration', headline: 'groups' }} />
        <KPI label="Hotspots (2+ vehicles)" value={fmtInt(d?.hotspots_total ?? d?.hotspots?.length)} icon={MapPin} color="red"
          hint={d?.hotspots_total > (d?.hotspots?.length ?? 0) ? `busiest ${fmtInt(d?.hotspots?.length)} mapped` : undefined} 
          drill={{ dataset: 'stops', params: { ...ctx, measure: 'vehicles' }, groups: ['spot'], sort: 'duration', note: 'spots of ~200 m where two or more vehicles stood' }} />
      </KPIGrid>

      <div className="grid grid-cols-1 xl:grid-cols-5 gap-6 mb-6">
        <Card title="Hotspots" icon={MapPin} className="xl:col-span-3"
          subtitle="Stops clustered on a ~200 m grid, sized by how many different vehicles stood there. A single truck's long halt cannot make a big circle; many trucks can.">
          <GeoMap height={420} onReady={setMap} />
        </Card>
        <Card title="Candidate fences" icon={CircleDot} className="xl:col-span-2"
          subtitle="The busiest hotspots. Places many vehicles stop at, outside every fence, are the first candidates for a new geofence.">
          <div className="max-h-[420px] overflow-y-auto">
            <DataTable dense rows={d?.hotspots ?? []}
              onRowClick={h => map?.setView([h.lat, h.lon], 16)}
              columns={[
                { key: 'loc', label: 'Location', render: h => <span className="font-mono text-xs">{Number(h.lat).toFixed(4)}, {Number(h.lon).toFixed(4)}</span> },
                { key: 'vehicles', label: 'Vehicles', align: 'right', render: h => fmtInt(h.vehicles) },
                { key: 'stops', label: 'Stops', align: 'right', render: h => fmtInt(h.stops) },
                { key: 'duration_s', label: 'Standing', align: 'right', render: h => fmtHours(h.duration_s, 0) },
              ]} />
          </div>
        </Card>
      </div>

      <Note>Click a candidate to zoom the map to it, then switch on satellite imagery to see what is there.</Note>

      <Card className="mt-6">
        <div className="mb-4">
          <Toolbar>
            <SearchInput value={q} onChange={reset(setQ)} placeholder="Vehicle or transporter" />
            <Select value={scope} onChange={reset(setScope)} options={[
              { value: 'outside', label: 'Outside every fence' }, { value: 'all', label: 'All stops' }]} />
            <Select value={min} onChange={reset(setMin)} options={[
              { value: '5', label: '5 min or more' }, { value: '15', label: '15 min or more' }, { value: '30', label: '30 min or more' },
              { value: '60', label: '1 hour or more' }, { value: '240', label: '4 hours or more' }]} />
            <DateInput label="From" value={from} onChange={reset(setFrom)} />
            <DateInput label="To" value={to} onChange={reset(setTo)} />
          </Toolbar>
        </div>
        {data.error ? <ErrorBox error={data.error} onRetry={data.reload} /> : (
          <>
            <DataTable rows={d?.items ?? []} loading={data.loading} sort={sort} order={order}
              onSort={k => { onSort(k); setPage(1); }}
              onRowClick={r => navigate(`/geo/trips/${r.i_trip_no}`)}
              columns={[
                { key: 'start', label: 'From', sortable: true, render: r => fmtDateTime(r.dt_start) },
                { key: 'duration', label: 'For', sortable: true, align: 'right', render: r => fmtDuration(r.i_duration_s) },
                { key: 'vehicle', label: 'Vehicle', sortable: true, render: r => r.s_asset_id },
                { key: 'where', label: 'Where', render: r => r.s_site_name
                  ? <span className="text-gray-200">{r.s_site_name}</span>
                  : <span className="font-mono text-xs text-amber-400">{Number(r.d_lat).toFixed(4)}, {Number(r.d_long).toFixed(4)}</span> },
                { key: 'transporter', label: 'Transporter', render: r => <span className="text-xs">{r.s_trans_name || '—'}</span> },
                { key: 'lane', label: 'Lane', render: r => <span className="text-xs text-gray-500">{r.s_origin ? `${r.s_origin} → ${r.s_destination}` : '—'}</span> },
                { key: 'trip', label: 'Trip', render: r => <TripRef trip={r.i_trip_no} trips={r.s_trips} /> },
              ]} />
            <Pagination page={page} pages={d?.pages ?? 1} total={d?.total} onPage={setPage} />
          </>
        )}
      </Card>
    </div>
  );
}
