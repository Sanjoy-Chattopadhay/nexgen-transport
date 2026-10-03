import { useNavigate } from 'react-router-dom';
import PageContainer from '../components/layout/PageContainer';
import SearchInput from '../components/ui/SearchInput';
import DataTable from '../components/ui/DataTable';
import Pagination from '../components/ui/Pagination';
import { useApi } from '../hooks/useApi';
import { usePagination } from '../hooks/usePagination';
import { listRoutes } from '../services/routes';
import { formatNumber, formatPercent, formatDuration, formatDistance, rateTextClass } from '../lib/formatters';
import type { PaginatedResponse } from '../types/common';
import type { RouteSummaryRow } from '../types/route';

export default function RouteList() {
  const navigate = useNavigate();
  const { page, setPage, limit, search, onSearch, sortBy, sortOrder, onSort } = usePagination('trip_count', 'desc');
  const { data, loading } = useApi<PaginatedResponse<RouteSummaryRow>>(
    () => listRoutes({ page, limit, search, sort_by: sortBy, sort_order: sortOrder }),
    [page, limit, search, sortBy, sortOrder]
  );

  const columns = [
    { key: 'route_name', label: 'Route Name' },
    { key: 'origin', label: 'Origin' },
    { key: 'destination', label: 'Destination' },
    { key: 'trip_count', label: 'Trip Count', sortable: true, render: (r: RouteSummaryRow) => formatNumber(r.trip_count) },
    { key: 'avg_duration_min', label: 'Avg Duration', sortable: true, render: (r: RouteSummaryRow) => formatDuration(r.avg_duration_min) },
    { key: 'eta_success_rate', label: 'ETA Rate', sortable: true, render: (r: RouteSummaryRow) => (
      <span className={rateTextClass(r.eta_success_rate)}>
        {formatPercent(r.eta_success_rate)}
      </span>
    )},
    { key: 'avg_distance_km', label: 'Avg Distance', sortable: true, render: (r: RouteSummaryRow) => formatDistance(r.avg_distance_km) },
  ];

  return (
    <PageContainer>
      <div className="mb-4 w-72">
        <SearchInput value={search} onChange={onSearch} placeholder="Search routes..." />
      </div>
      <DataTable
        columns={columns}
        data={data?.data || []}
        loading={loading}
        sortBy={sortBy}
        sortOrder={sortOrder}
        onSort={onSort}
        onRowClick={(r) => navigate(`/routes/${encodeURIComponent(r.origin)}/${encodeURIComponent(r.destination)}`)}
      />
      {data && <Pagination page={data.page} totalPages={data.total_pages} onPageChange={setPage} />}
    </PageContainer>
  );
}
