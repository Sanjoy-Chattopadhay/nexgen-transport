import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { api } from '../lib/api';
import { useApi } from '../hooks/useApi';
import { Card, DataTable, DateInput, ErrorBox, PageHeader, Pagination, SearchInput, Toolbar, useSort } from '../components/ui';
import { fmtInt, fmtNum } from '../lib/format';

export default function DriverList() {
  const navigate = useNavigate();
  const [q, setQ] = useState('');
  const [from, setFrom] = useState('');
  const [to, setTo] = useState('');
  const [page, setPage] = useState(1);
  const { sort, order, onSort } = useSort('trips');
  const list = useApi(() => api.drivers({ q, from, to, sort, order, page, page_size: 40 }), [q, from, to, sort, order, page]);

  return (
    <div className="animate-fade-in">
      <PageHeader title="Drivers"
        subtitle="Drivers as the fleet system records them against each trip, with the site-rule breaches the geofences caught on their trips." />
      <Card>
        <div className="mb-4">
          <Toolbar>
            <SearchInput value={q} onChange={v => { setQ(v); setPage(1); }} placeholder="Driver name or mobile" />
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
              onSort={k => { onSort(k); setPage(1); }} rowKey={r => r.driver}
              onRowClick={r => navigate(`/geo/drivers/detail?name=${encodeURIComponent(r.driver)}`)}
              columns={[
                { key: 'driver', label: 'Driver', sortable: true, render: r => (
                  <div><p className="text-gray-100 font-medium">{r.driver}</p><p className="text-xs text-gray-500">{r.mobile || ''}</p></div>
                ) },
                { key: 'transporter', label: 'Transporter', sortable: true, render: r => <span className="text-xs">{r.transporter || '—'}</span> },
                { key: 'trips', label: 'Trips', sortable: true, align: 'right', render: r => fmtInt(r.trips) },
                { key: 'vehicles', label: 'Vehicles', sortable: true, align: 'right', render: r => fmtInt(r.vehicles) },
                { key: 'facility_visits', label: 'Facility visits', sortable: true, align: 'right', render: r => fmtInt(r.facility_visits) },
                { key: 'overspeed', label: 'Overspeed', sortable: true, align: 'right', render: r => r.overspeed ? <span className="text-amber-400">{r.overspeed}</span> : '0' },
                { key: 'restricted', label: 'Restricted', sortable: true, align: 'right', render: r => r.restricted ? <span className="text-red-400">{r.restricted}</span> : '0' },
                { key: 'violations_per_100_trips', label: 'Alerts /100 trips', sortable: true, align: 'right', render: r => fmtNum(r.violations_per_100_trips) },
              ]} />
            <Pagination page={page} pages={list.data?.pages ?? 1} total={list.data?.total} onPage={setPage} />
          </>
        )}
      </Card>
    </div>
  );
}
