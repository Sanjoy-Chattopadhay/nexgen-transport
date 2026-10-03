import { useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Building2, Search, Target, Truck } from 'lucide-react';
import PageContainer from '../components/layout/PageContainer';
import DataTable from '../components/ui/DataTable';
import { useApi } from '../hooks/useApi';
import { listConsignees, type ConsigneeRow } from '../services/partners';
import { formatNumber, formatPercent, formatSpeed, formatDistance, formatDate } from '../lib/formatters';

const otdClass = (v: number | null) =>
  v == null ? 'text-gray-500' : v >= 95 ? 'text-emerald-400 font-semibold' : v >= 80 ? 'text-amber-400' : 'text-red-400 font-semibold';

export default function ConsigneeList() {
  const navigate = useNavigate();
  const [search, setSearch] = useState('');
  const [sortBy, setSortBy] = useState('trips');
  const [sortOrder, setSortOrder] = useState<'asc' | 'desc'>('desc');
  const { data, loading } = useApi(() => listConsignees(), []);
  const all = data?.data ?? [];

  const rows = useMemo(() => {
    const f = all.filter(c => c.consignee.toLowerCase().includes(search.toLowerCase()));
    return f.sort((a: any, b: any) => {
      const av = a[sortBy] ?? -Infinity, bv = b[sortBy] ?? -Infinity;
      if (typeof av === 'string') return sortOrder === 'asc' ? av.localeCompare(bv) : bv.localeCompare(av);
      return sortOrder === 'asc' ? av - bv : bv - av;
    });
  }, [all, search, sortBy, sortOrder]);

  const onSort = (c: string) => {
    if (sortBy === c) setSortOrder(o => o === 'asc' ? 'desc' : 'asc');
    else { setSortBy(c); setSortOrder('desc'); }
  };

  const totalTrips = all.reduce((s, c) => s + c.trips, 0);

  return (
    <PageContainer>
      <div className="flex flex-wrap items-center gap-6 mb-6">
        <Stat icon={Building2} color="text-blue-400" label="Consignees" value={formatNumber(all.length)} />
        <Stat icon={Truck} color="text-purple-400" label="Total Trips" value={formatNumber(totalTrips)} />
        <Stat icon={Target} color="text-emerald-400" label="Fleet On-Time"
          value={all.length ? formatPercent(all.reduce((s, c) => s + (c.otd_pct ?? 0) * c.trips, 0) / (totalTrips || 1)) : '—'} />
        <div className="relative ml-auto">
          <Search className="w-4 h-4 text-gray-500 absolute left-3 top-1/2 -translate-y-1/2" />
          <input value={search} onChange={e => setSearch(e.target.value)} placeholder="Search consignee…"
            className="pl-10 pr-3 py-2 bg-gray-800 border border-gray-700 rounded-lg text-sm text-gray-200 focus:outline-none focus:border-blue-500 w-64" />
        </div>
      </div>

      <div className="bg-gray-900 rounded-xl border border-gray-800 p-2">
        <DataTable<ConsigneeRow>
          loading={loading} sortBy={sortBy} sortOrder={sortOrder} onSort={onSort}
          onRowClick={(r) => navigate(`/consignees/${encodeURIComponent(r.consignee)}`)}
          emptyMessage={search ? `No consignees matching "${search}"` : 'No consignee data available.'}
          columns={[
            { key: 'consignee', label: 'Consignee', sortable: true, render: r => (
              <span className="flex items-center gap-2"><Building2 className="w-4 h-4 text-blue-400 shrink-0" />
                <span className="text-white font-medium truncate max-w-[280px]">{r.consignee}</span></span>) },
            { key: 'trips', label: 'Trips', sortable: true },
            { key: 'otd_pct', label: 'On-Time %', sortable: true, render: r => <span className={otdClass(r.otd_pct)}>{formatPercent(r.otd_pct)}</span> },
            { key: 'total_km', label: 'Distance', sortable: true, render: r => formatDistance(r.total_km) },
            { key: 'avg_speed', label: 'Avg Speed', sortable: true, render: r => formatSpeed(r.avg_speed) },
            { key: 'destinations', label: 'Destinations', sortable: true },
            { key: 'vehicles', label: 'Vehicles', sortable: true },
            { key: 'last_trip', label: 'Last Trip', sortable: true, render: r => formatDate(r.last_trip) },
          ]}
          data={rows}
        />
      </div>
    </PageContainer>
  );
}

function Stat({ icon: Icon, color, label, value }: { icon: any; color: string; label: string; value: string }) {
  return (
    <div className="flex items-center gap-2">
      <Icon className={`w-5 h-5 ${color}`} />
      <span className="text-sm text-gray-400">{label}: <span className="text-white font-semibold">{value}</span></span>
    </div>
  );
}
