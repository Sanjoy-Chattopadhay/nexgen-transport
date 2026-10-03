import { useMemo, useState } from 'react';
import { Search, Download } from 'lucide-react';
import { Link } from 'react-router-dom';
import PageContainer from '../../components/layout/PageContainer';
import Spinner from '../../components/ui/Spinner';
import DataTable from '../../components/ui/DataTable';
import { useApi } from '../../hooks/useApi';
import { useTTAFilters } from '../../components/tta/dashboard/FilterContext';
import { getDashRecords } from '../../services/ttaDashboard';
import { downloadCsv } from '../../lib/csv';

const LIMITS = [250, 500, 1000, 2500, 5000];

export default function Explorer() {
  const { params, paramsKey } = useTTAFilters();
  const [limit, setLimit] = useState(500);
  const [query, setQuery] = useState('');

  const { data, loading } = useApi(() => getDashRecords(limit, params), [paramsKey, limit]);

  // frontend-only substring match across all columns
  const rows = useMemo(() => {
    const all = data?.rows ?? [];
    if (!query.trim()) return all;
    const q = query.toLowerCase();
    return all.filter((r: any) => Object.values(r).some(v => v != null && String(v).toLowerCase().includes(q)));
  }, [data, query]);

  return (
    <PageContainer title="🔎 Data Explorer">
      <div className="bg-gray-900 rounded-xl border border-gray-800 p-5">
        <div className="flex items-center gap-3 flex-wrap mb-4">
          <div className="relative flex-1 min-w-[220px] max-w-md">
            <Search className="w-4 h-4 text-gray-500 absolute left-3 top-1/2 -translate-y-1/2" />
            <input value={query} onChange={e => setQuery(e.target.value)}
              placeholder="Quick search across all columns…"
              className="w-full bg-gray-800 border border-gray-700 rounded-lg pl-9 pr-3 py-2 text-xs text-gray-200 outline-none focus:border-blue-600" />
          </div>
          <label className="flex items-center gap-2 text-xs text-gray-400">
            Rows to load
            <select value={limit} onChange={e => setLimit(Number(e.target.value))}
              className="bg-gray-800 border border-gray-700 rounded-lg px-2 py-1.5 text-xs text-gray-300 outline-none">
              {LIMITS.map(l => <option key={l} value={l}>{l}</option>)}
            </select>
          </label>
          <button onClick={() => downloadCsv(rows, 'trip_records.csv')}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs text-gray-300 border border-gray-700 bg-gray-800 hover:text-white">
            <Download className="w-3.5 h-3.5" /> Export CSV
          </button>
          <span className="ml-auto text-xs text-gray-500">
            {data ? `showing ${rows.length.toLocaleString('en-IN')} of ${data.total.toLocaleString('en-IN')} filtered trips (newest first)` : ''}
          </span>
        </div>

        {loading ? <Spinner /> : (
          <div className="max-h-[640px] overflow-auto">
            <DataTable columns={[
              {
                key: 'trip_id', label: 'Trip',
                render: (r: any) => <Link to={`/trips/${r.trip_id}`} className="text-blue-400 hover:underline">{r.trip_id}</Link>,
              },
              { key: 'dept_dt', label: 'Departed' },
              { key: 'transporter', label: 'Transporter' },
              { key: 'vehicle_no', label: 'Vehicle', render: (r: any) => <span className="font-mono text-xs">{r.vehicle_no}</span> },
              { key: 'vehicle_category', label: 'Category' },
              { key: 'own_market', label: 'Own/Mkt' },
              { key: 'consignor', label: 'Consignor' },
              { key: 'destination', label: 'Destination' },
              { key: 'driver_name', label: 'Driver' },
              { key: 'eta_dt', label: 'ETA' },
              { key: 'ata_dt', label: 'ATA' },
              {
                key: 'delivery_status', label: 'Delivery',
                render: (r: any) => r.delivery_status
                  ? <span className={String(r.delivery_status).toLowerCase().includes('on time') ? 'text-emerald-400' : 'text-red-400'}>{r.delivery_status}</span> : '—',
              },
              { key: 'transit_hours', label: 'Transit (h)' },
              { key: 'planned_transit_hours', label: 'Planned (h)' },
              { key: 'detention_hours', label: 'Detention (h)' },
              { key: 'distance_km', label: 'Km' },
              { key: 'avg_speed_kmph', label: 'Speed' },
              { key: 'speed_violations', label: 'Alerts' },
              { key: 'gps_uptime', label: 'GPS %' },
              { key: 'trip_status', label: 'Status' },
            ]} data={rows} emptyMessage="No trips match the filters/search" />
          </div>
        )}
      </div>
    </PageContainer>
  );
}
