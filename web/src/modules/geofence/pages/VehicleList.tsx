import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { api } from '../lib/api';
import { useApi } from '../hooks/useApi';
import {
  Badge, Card, DataTable, DateInput, ErrorBox, PageHeader, Pagination, SearchInput, Toolbar, useSort,
} from '../components/ui';
import { fmtDateTime, fmtDuration, fmtInt, fmtKm } from '../lib/format';

export default function VehicleList() {
  const navigate = useNavigate();
  const [q, setQ] = useState('');
  const [from, setFrom] = useState('');
  const [to, setTo] = useState('');
  const [page, setPage] = useState(1);
  const { sort, order, onSort } = useSort('trips');
  const list = useApi(() => api.vehicles({ q, from, to, sort, order, page, page_size: 30 }), [q, from, to, sort, order, page]);

  return (
    <div className="animate-fade-in">
      <PageHeader title="Vehicles"
        subtitle="Every truck that reported GPS, with its trips, the time it spent at facilities, and its alerts. Live position is shown when the live detector is running." />
      <Card>
        <div className="mb-4">
          <Toolbar>
            <SearchInput value={q} onChange={v => { setQ(v); setPage(1); }} placeholder="Vehicle number or transporter" />
            <DateInput label="From" value={from} onChange={v => { setFrom(v); setPage(1); }} />
            <DateInput label="To" value={to} onChange={v => { setTo(v); setPage(1); }} />
          </Toolbar>
        </div>
        {list.data?.window?.defaulted && (
          <p className="text-xs text-amber-400/80 mb-3">
            Showing the latest {list.data.window.days} days ({String(list.data.window.from).slice(0, 10)} to {String(list.data.window.to).slice(0, 10)}): pick dates to see more.
          </p>
        )}
        {list.error ? <ErrorBox error={list.error} onRetry={list.reload} /> : (
          <>
            <DataTable rows={list.data?.items ?? []} loading={list.loading} sort={sort} order={order}
              onSort={k => { onSort(k); setPage(1); }} rowKey={r => r.s_asset_id}
              onRowClick={r => navigate(`/geo/vehicles/${encodeURIComponent(r.s_asset_id)}`)}
              columns={[
                { key: 's_asset_id', label: 'Vehicle', sortable: true, render: r => (
                  <div><p className="text-gray-100 font-medium">{r.s_asset_id}</p><p className="text-xs text-gray-500">{r.s_asset_type || ''}</p></div>
                ) },
                { key: 'transporter', label: 'Transporter', sortable: true, render: r => <span className="text-xs">{r.transporter || '—'}</span> },
                { key: 'trips', label: 'Trips', sortable: true, align: 'right', render: r => fmtInt(r.trips) },
                { key: 'facility_visits', label: 'Facility visits', sortable: true, align: 'right', render: r => fmtInt(r.facility_visits) },
                { key: 'facility_dwell_s', label: 'At facilities', sortable: true, align: 'right', render: r => fmtDuration(r.facility_dwell_s) },
                { key: 'origin_dwell_p50_s', label: 'Median at loading place', sortable: true, align: 'right', render: r => fmtDuration(r.origin_dwell_p50_s) },
                { key: 'distance_km', label: 'Distance', sortable: true, align: 'right', render: r => fmtKm(r.distance_km) },
                { key: 'violations', label: 'Alerts', sortable: true, align: 'right', render: r => r.violations ? <span className="text-red-400">{fmtInt(r.violations)}</span> : '0' },
                { key: 'last_site', label: 'Last place', render: r => <span className="text-xs text-gray-400">{r.last_site || '—'}</span> },
                { key: 'last_seen', label: 'Last GPS', sortable: true, render: r => (
                  <div className="text-xs">
                    {fmtDateTime(r.last_seen)}
                    {r.live && <p><Badge variant={r.live.s_site_name ? 'success' : 'info'}>live{r.live.s_site_name ? ` · ${r.live.s_site_name}` : ''}</Badge></p>}
                  </div>
                ) },
              ]} />
            <Pagination page={page} pages={list.data?.pages ?? 1} total={list.data?.total} onPage={setPage} />
          </>
        )}
      </Card>
    </div>
  );
}
