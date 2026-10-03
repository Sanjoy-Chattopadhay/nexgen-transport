import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { api } from '../lib/api';
import { useApi } from '../hooks/useApi';
import { Card, DataTable, DateInput, ErrorBox, Note, PageHeader, Pagination, SearchInput, Toolbar, useSort } from '../components/ui';
import { fmtDuration, fmtInt, fmtNum, fmtPct, share } from '../lib/format';

export default function TransporterList() {
  const navigate = useNavigate();
  const [q, setQ] = useState('');
  const [from, setFrom] = useState('');
  const [to, setTo] = useState('');
  const [page, setPage] = useState(1);
  const { sort, order, onSort } = useSort('trips');
  const list = useApi(() => api.transporters({ q, from, to, sort, order, page, page_size: 40 }), [q, from, to, sort, order, page]);

  return (
    <div className="animate-fade-in">
      <PageHeader title="Transporters"
        subtitle="Carriers compared on what the geofences saw: how long their trucks sit at the loading place, how long they take between places, how often they breach site rules, and how reliable their GPS is." />
      <div className="mb-4">
        <Note>
          Times are <b className="text-gray-300">medians</b>, not averages: a handful of trucks held for days would otherwise set the
          "typical" figure. <b className="text-gray-300">Left after gate-out</b> is how long after the fleet system's gate-out stamp the
          truck physically left the first place — time the stamp does not show.
        </Note>
      </div>
      <Card>
        <div className="mb-4">
          <Toolbar>
            <SearchInput value={q} onChange={v => { setQ(v); setPage(1); }} placeholder="Transporter name" />
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
              onSort={k => { onSort(k); setPage(1); }} rowKey={r => r.transporter}
              onRowClick={r => navigate(`/geo/transporters/detail?name=${encodeURIComponent(r.transporter)}`)}
              columns={[
                { key: 'transporter', label: 'Transporter', sortable: true, render: r => <span className="text-gray-100 font-medium">{r.transporter}</span> },
                { key: 'trips', label: 'Trips', sortable: true, align: 'right', render: r => fmtInt(r.trips) },
                { key: 'vehicles', label: 'Vehicles', sortable: true, align: 'right', render: r => fmtInt(r.vehicles) },
                { key: 'origin_dwell_p50_s', label: 'At loading place', sortable: true, align: 'right', render: r => fmtDuration(r.origin_dwell_p50_s) },
                { key: 'exit_after_gate_out_p50_s', label: 'Left after gate-out', sortable: true, align: 'right', render: r => fmtDuration(r.exit_after_gate_out_p50_s) },
                { key: 'transit_p50_s', label: 'Transit', sortable: true, align: 'right', render: r => fmtDuration(r.transit_p50_s) },
                { key: 'dest_dwell_p50_s', label: 'At destination', sortable: true, align: 'right', render: r => fmtDuration(r.dest_dwell_p50_s) },
                { key: 'violations_per_100_trips', label: 'Alerts /100 trips', sortable: true, align: 'right', render: r => fmtNum(r.violations_per_100_trips) },
                { key: 'broken_trips', label: 'Broken GPS', sortable: true, align: 'right', render: r => fmtPct(share(r.broken_trips, r.trips), 0) },
              ]} />
            <Pagination page={page} pages={list.data?.pages ?? 1} total={list.data?.total} onPage={setPage} />
          </>
        )}
      </Card>
    </div>
  );
}
