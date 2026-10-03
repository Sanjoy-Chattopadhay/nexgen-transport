import { useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Factory, Truck, Users } from 'lucide-react';
import PageContainer from '../components/layout/PageContainer';
import DataTable from '../components/ui/DataTable';
import { useApi } from '../hooks/useApi';
import { getConsignorsOverview, type ConsignorRow } from '../services/partners';
import { formatNumber, formatPercent, formatSpeed, formatDistance, formatDate } from '../lib/formatters';

const otdClass = (v: number | null) =>
  v == null ? 'text-gray-500' : v >= 95 ? 'text-emerald-400 font-semibold' : v >= 80 ? 'text-amber-400' : 'text-red-400 font-semibold';

export default function ConsignorList() {
  const navigate = useNavigate();
  const [sortBy, setSortBy] = useState('trips');
  const [sortOrder, setSortOrder] = useState<'asc' | 'desc'>('desc');
  const { data, loading } = useApi(() => getConsignorsOverview(), []);
  const all = data?.data ?? [];

  const rows = useMemo(() => [...all].sort((a: any, b: any) => {
    const av = a[sortBy] ?? -Infinity, bv = b[sortBy] ?? -Infinity;
    if (typeof av === 'string') return sortOrder === 'asc' ? av.localeCompare(bv) : bv.localeCompare(av);
    return sortOrder === 'asc' ? av - bv : bv - av;
  }), [all, sortBy, sortOrder]);

  const onSort = (c: string) => {
    if (sortBy === c) setSortOrder(o => o === 'asc' ? 'desc' : 'asc');
    else { setSortBy(c); setSortOrder('desc'); }
  };

  const totalTrips = all.reduce((s, c) => s + c.trips, 0);

  return (
    <PageContainer>
      <div className="flex flex-wrap items-center gap-6 mb-6">
        <Stat icon={Factory} color="text-amber-400" label="Consignors" value={formatNumber(all.length)} />
        <Stat icon={Truck} color="text-purple-400" label="Total Trips" value={formatNumber(totalTrips)} />
        <Stat icon={Users} color="text-blue-400" label="Consignees Served" value={formatNumber(all.reduce((s, c) => s + c.consignees, 0))} />
      </div>

      <div className="bg-gray-900 rounded-xl border border-gray-800 p-2">
        <DataTable<ConsignorRow>
          loading={loading} sortBy={sortBy} sortOrder={sortOrder} onSort={onSort}
          onRowClick={(r) => navigate(`/consignors/${r.id}`)}
          emptyMessage="No consignor data available."
          columns={[
            { key: 'name', label: 'Consignor', sortable: true, render: r => (
              <span className="flex items-center gap-2"><Factory className="w-4 h-4 text-amber-400 shrink-0" />
                <span className="text-white font-medium truncate max-w-[280px]">{r.name}</span></span>) },
            { key: 'trips', label: 'Trips', sortable: true },
            { key: 'otd_pct', label: 'On-Time %', sortable: true, render: r => <span className={otdClass(r.otd_pct)}>{formatPercent(r.otd_pct)}</span> },
            { key: 'consignees', label: 'Consignees', sortable: true },
            { key: 'total_km', label: 'Distance', sortable: true, render: r => formatDistance(r.total_km) },
            { key: 'avg_speed', label: 'Avg Speed', sortable: true, render: r => formatSpeed(r.avg_speed) },
            { key: 'vehicles', label: 'Vehicles', sortable: true },
            { key: 'drivers', label: 'Drivers', sortable: true },
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
