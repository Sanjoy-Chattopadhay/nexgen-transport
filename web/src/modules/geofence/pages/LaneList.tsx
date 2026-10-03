import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { api } from '../lib/api';
import { useApi } from '../hooks/useApi';
import { Card, DataTable, DateInput, ErrorBox, Note, PageHeader, Pagination, SearchInput, Toolbar, useSort } from '../components/ui';
import { fmtDuration, fmtInt, fmtNum, fmtPct, share } from '../lib/format';

export default function LaneList() {
  const navigate = useNavigate();
  const [q, setQ] = useState('');
  const [from, setFrom] = useState('');
  const [to, setTo] = useState('');
  const [page, setPage] = useState(1);
  const { sort, order, onSort } = useSort('trips');
  const list = useApi(() => api.lanes({ q, from, to, sort, order, page, page_size: 40 }), [q, from, to, sort, order, page]);

  return (
    <div className="animate-fade-in">
      <PageHeader title="Lanes"
        subtitle="Origin → destination as the fleet system names them, measured by the places the geofences actually saw at each end of the trip." />
      <div className="mb-4">
        <Note>
          A lane's <b className="text-gray-300">usual first / last place</b> is the facility fence most trips on it were first and last
          seen at. When that is not the named origin or destination, either the naming or the fence master needs a look.
        </Note>
      </div>
      <Card>
        <div className="mb-4">
          <Toolbar>
            <SearchInput value={q} onChange={v => { setQ(v); setPage(1); }} placeholder="Origin or destination" />
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
              onSort={k => { onSort(k); setPage(1); }} rowKey={r => `${r.origin}|${r.destination}`}
              onRowClick={r => navigate(`/geo/lanes/detail?origin=${encodeURIComponent(r.origin)}&destination=${encodeURIComponent(r.destination)}`)}
              columns={[
                { key: 'origin', label: 'Lane', sortable: true, render: r => (
                  <span className="text-gray-100 font-medium">{r.origin} <span className="text-gray-600">→</span> {r.destination}</span>
                ) },
                { key: 'trips', label: 'Trips', sortable: true, align: 'right', render: r => fmtInt(r.trips) },
                { key: 'trips_with_lane', label: 'Both ends seen', sortable: true, align: 'right',
                  render: r => fmtPct(share(r.trips_with_lane, r.trips), 0) },
                { key: 'usual_first_place', label: 'Usual first place', render: r => <span className="text-xs">{r.usual_first_place || '—'}</span> },
                { key: 'usual_last_place', label: 'Usual last place', render: r => <span className="text-xs">{r.usual_last_place || '—'}</span> },
                { key: 'origin_dwell_p50_s', label: 'At origin', sortable: true, align: 'right', render: r => fmtDuration(r.origin_dwell_p50_s) },
                { key: 'transit_p50_s', label: 'Transit', sortable: true, align: 'right', render: r => fmtDuration(r.transit_p50_s) },
                { key: 'dest_dwell_p50_s', label: 'At destination', sortable: true, align: 'right', render: r => fmtDuration(r.dest_dwell_p50_s) },
                { key: 'transporters', label: 'Carriers', sortable: true, align: 'right', render: r => fmtInt(r.transporters) },
                { key: 'violations_per_100_trips', label: 'Alerts /100', sortable: true, align: 'right', render: r => fmtNum(r.violations_per_100_trips) },
              ]} />
            <Pagination page={page} pages={list.data?.pages ?? 1} total={list.data?.total} onPage={setPage} />
          </>
        )}
      </Card>
    </div>
  );
}
