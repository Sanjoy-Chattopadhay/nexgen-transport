import { useMemo, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { AlertTriangle, Building2, CalendarDays, Gauge, Hexagon, ShieldAlert, Truck } from 'lucide-react';
import { api } from '../lib/api';
import { useApi } from '../hooks/useApi';
import {
  Card, DataTable, DateInput, EntityLink, ErrorBox, KPI, Note, PageHeader, Pagination, SearchInput, Select, Toolbar,
  TripRef, useSort,
} from '../components/ui';
import { KPIGrid } from '../components/drill';
import { BarList, PALETTE, SeriesChart } from '../components/charts';
import { KIND_LABEL, fmtDateTime, fmtInt, fmtDayShort } from '../lib/format';

export default function Alerts() {
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const [kind, setKind] = useState('');
  const [q, setQ] = useState('');
  const [from, setFrom] = useState(params.get('from') || '');
  const [to, setTo] = useState(params.get('to') || '');
  const [page, setPage] = useState(1);
  const { sort, order, onSort } = useSort('time');
  const data = useApi(() => api.alerts({ kind, q, from, to, sort, order, page, page_size: 50 }),
    [kind, q, from, to, sort, order, page]);
  const d = data.data;

  const byDay = useMemo(() => {
    const m = new Map<string, any>();
    for (const r of d?.by_day ?? []) {
      const row = m.get(r.d_day) || { d_day: r.d_day, overspeed: 0, restricted: 0 };
      if (r.s_kind === 'overspeed') row.overspeed += r.n; else row.restricted += r.n;
      m.set(r.d_day, row);
    }
    return [...m.values()];
  }, [d]);
  const count = (k: string) => d?.by_kind?.find((x: any) => x.s_kind === k)?.n ?? 0;
  const reset = (fn: (v: string) => void) => (v: string) => { fn(v); setPage(1); };

  return (
    <div className="animate-fade-in">
      <PageHeader title="Alerts"
        subtitle="Site-rule breaches the geofences caught: entries into restricted and high-risk zones, and speeding inside a site. One alert per breach per visit, never one per GPS fix." />

      <KPIGrid className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-6">
        <KPI label="All alerts" value={fmtInt(d?.total)} icon={AlertTriangle} color="red" 
          drill={{ dataset: 'alerts', params: { kind, q, from, to }, groups: ['kind', 'site', 'transporter', 'vehicle', 'day'], sort: 'time' }} />
        <KPI label="Restricted-zone entries" value={fmtInt(count('restricted_entry'))} icon={ShieldAlert} color="red" 
          drill={{ dataset: 'alerts', params: { kind, q, from, to, preset: 'restricted_entry' }, groups: ['site', 'transporter', 'vehicle', 'hour'], sort: 'time' }} />
        <KPI label="High-risk-zone entries" value={fmtInt(count('high_risk_entry'))} icon={ShieldAlert} color="amber" 
          drill={{ dataset: 'alerts', params: { kind, q, from, to, preset: 'high_risk_entry' }, groups: ['site', 'transporter', 'vehicle', 'hour'], sort: 'time' }} />
        <KPI label="Overspeed inside sites" value={fmtInt(count('overspeed'))} icon={Gauge} color="amber" 
          drill={{ dataset: 'alerts', params: { kind, q, from, to, preset: 'overspeed' }, groups: ['site', 'transporter', 'vehicle', 'hour'], sort: 'excess' }} />
      </KPIGrid>

      <div className="mb-6">
        <Note tone="warn" title="Two restricted zones are in the master twice.">
          MARINE DRSTART and MARINE DREND each exist under two site ids with the same polygon, so every truck passing
          there raises two identical alerts. Counts for those two zones are double until one copy of each is deactivated.
        </Note>
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-3 gap-6 mb-6">
        <Card title="Day by day" icon={CalendarDays} className="xl:col-span-2">
          <SeriesChart data={byDay} x="d_day" height={220} xFormat={v => fmtDayShort(v)}
            series={[
              { key: 'restricted', label: 'Zone entries', color: PALETTE.red, stack: 'a' },
              { key: 'overspeed', label: 'Overspeed', color: PALETTE.amber, stack: 'a' },
            ]} />
        </Card>
        <Card title="Where" icon={Hexagon}>
          <BarList rows={(d?.by_site ?? []).slice(0, 10).map((s: any, i: number) => ({
            key: `${s.i_site_id}-${s.s_kind}-${i}`, label: s.s_site_name, value: s.n,
            sub: KIND_LABEL[s.s_kind]?.split(' ')[0], onClick: () => navigate(`/geo/geofences/${s.i_site_id}`),
          }))} />
        </Card>
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-2 gap-6 mb-6">
        <Card title="Transporters" icon={Building2}>
          <BarList rows={(d?.by_transporter ?? []).map((t: any) => ({
            key: t.transporter, label: t.transporter, value: t.n, sub: `${t.vehicles} veh`,
            onClick: () => navigate(`/geo/transporters/detail?name=${encodeURIComponent(t.transporter)}`),
          }))} />
        </Card>
        <Card title="Vehicles" icon={Truck}>
          <BarList rows={(d?.by_vehicle ?? []).map((v: any) => ({
            key: v.s_asset_id, label: v.s_asset_id, value: v.n,
            onClick: () => navigate(`/geo/vehicles/${encodeURIComponent(v.s_asset_id)}`),
          }))} />
        </Card>
      </div>

      <Card>
        <div className="mb-4">
          <Toolbar>
            <SearchInput value={q} onChange={reset(setQ)} placeholder="Vehicle, site, driver, transporter" />
            <Select value={kind} onChange={reset(setKind)} options={[
              { value: '', label: 'All kinds' },
              { value: 'restricted_entry', label: 'Restricted-zone entry' },
              { value: 'high_risk_entry', label: 'High-risk-zone entry' },
              { value: 'overspeed', label: 'Overspeed in site' },
            ]} />
            <DateInput label="From" value={from} onChange={reset(setFrom)} />
            <DateInput label="To" value={to} onChange={reset(setTo)} />
          </Toolbar>
        </div>
        {data.error ? <ErrorBox error={data.error} onRetry={data.reload} /> : (
          <>
            <DataTable rows={d?.items ?? []} loading={data.loading} sort={sort} order={order}
              onSort={k => { onSort(k); setPage(1); }} rowKey={r => r.id}
              onRowClick={r => navigate(`/geo/trips/${r.i_trip_no}`)}
              columns={[
                { key: 'time', label: 'When', sortable: true, render: r => fmtDateTime(r.dt_event) },
                { key: 'kind', label: 'What', sortable: true, render: r => (
                  <span className={r.s_kind === 'overspeed' ? 'text-amber-400' : 'text-red-400'}>{KIND_LABEL[r.s_kind] || r.s_kind}</span>
                ) },
                { key: 'site', label: 'Where', sortable: true, render: r => <EntityLink to={`/geo/geofences/${r.i_site_id}`}>{r.s_site_name}</EntityLink> },
                { key: 'vehicle', label: 'Vehicle', sortable: true, render: r => r.s_asset_id },
                { key: 'excess', label: 'Speed / limit', sortable: true, align: 'right',
                  render: r => r.i_observed ? <span><b className="text-amber-400">{r.i_observed}</b> / {r.i_limit} km/h</span> : '—' },
                { key: 'driver', label: 'Driver', render: r => <span className="text-xs">{r.s_driver_name || '—'}</span> },
                { key: 'transporter', label: 'Transporter', render: r => <span className="text-xs text-gray-400">{r.s_trans_name || '—'}</span> },
                { key: 'trip', label: 'Trip', render: r => <TripRef trip={r.i_trip_no} trips={r.s_trips} /> },
              ]} />
            <Pagination page={page} pages={d?.pages ?? 1} total={d?.total} onPage={setPage} />
          </>
        )}
      </Card>
    </div>
  );
}
