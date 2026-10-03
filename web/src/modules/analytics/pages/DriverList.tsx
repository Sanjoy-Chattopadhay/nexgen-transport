import { useNavigate } from 'react-router-dom';
import PageContainer from '../components/layout/PageContainer';
import SearchInput from '../components/ui/SearchInput';
import DataTable from '../components/ui/DataTable';
import Pagination from '../components/ui/Pagination';
import { useApi } from '../hooks/useApi';
import { usePagination } from '../hooks/usePagination';
import { listDrivers } from '../services/drivers';
import { formatNumber, formatPercent, formatSpeed, formatDistance, rateTextClass } from '../lib/formatters';
import type { PaginatedResponse } from '../types/common';
import type { DriverSummaryRow } from '../types/driver';

function ScoreBadge({ score }: { score: number | null | undefined }) {
  if (score == null) return <span className="text-gray-600 text-xs">-</span>;
  const color = score >= 70 ? 'text-emerald-400 bg-emerald-950/50' :
                score >= 50 ? 'text-amber-400 bg-amber-950/50' :
                'text-red-400 bg-red-950/50';
  return (
    <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-bold ${color}`}>
      {score.toFixed(1)}
    </span>
  );
}

export default function DriverList() {
  const navigate = useNavigate();
  const { page, setPage, limit, search, onSearch, sortBy, sortOrder, onSort } = usePagination('total_trips', 'desc');
  const { data, loading } = useApi<PaginatedResponse<DriverSummaryRow>>(
    () => listDrivers({ page, limit, search, sort_by: sortBy, sort_order: sortOrder }),
    [page, limit, search, sortBy, sortOrder]
  );

  const columns = [
    { key: 'driver_name', label: 'Driver Name' },
    { key: 'driver_mobile', label: 'Mobile' },
    { key: 'score', label: 'Score', render: (r: DriverSummaryRow) => (
      <ScoreBadge score={(r as any).composite_score ?? null} />
    )},
    { key: 'total_trips', label: 'Total Trips', sortable: true, render: (r: DriverSummaryRow) => formatNumber(r.total_trips) },
    { key: 'eta_success_rate', label: 'ETA Rate', sortable: true, render: (r: DriverSummaryRow) => (
      <span className={rateTextClass(r.eta_success_rate)}
            title={r.eta_known_count != null && r.eta_known_count < r.total_trips
              ? `Covers ${r.eta_known_count} of ${r.total_trips} trips — the rest never recorded an arrival`
              : undefined}>
        {formatPercent(r.eta_success_rate)}
        {r.eta_known_count != null && r.eta_known_count < r.total_trips && (
          <span className="ml-1 text-xs text-gray-500">of {r.eta_known_count}</span>
        )}
      </span>
    )},
    { key: 'avg_speed_kmph', label: 'Avg Speed', sortable: true, render: (r: DriverSummaryRow) => formatSpeed(r.avg_speed_kmph) },
    { key: 'vehicles_used', label: 'Vehicles', render: (r: DriverSummaryRow) => formatNumber(r.vehicles_used) },
    { key: 'total_distance_km', label: 'Total Distance', sortable: true, render: (r: DriverSummaryRow) => formatDistance(r.total_distance_km) },
  ];

  return (
    <PageContainer>
      <div className="mb-4 w-72">
        <SearchInput value={search} onChange={onSearch} placeholder="Search drivers..." />
      </div>
      <DataTable
        columns={columns}
        data={data?.data || []}
        loading={loading}
        sortBy={sortBy}
        sortOrder={sortOrder}
        onSort={onSort}
        onRowClick={(r) => navigate(`/drivers/${r.driver_id}`)}
      />
      {data && <Pagination page={data.page} totalPages={data.total_pages} onPageChange={setPage} />}
    </PageContainer>
  );
}
