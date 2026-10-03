import { useNavigate } from 'react-router-dom';
import PageContainer from '../components/layout/PageContainer';
import SearchInput from '../components/ui/SearchInput';
import DataTable from '../components/ui/DataTable';
import Pagination from '../components/ui/Pagination';
import { useApi } from '../hooks/useApi';
import { usePagination } from '../hooks/usePagination';
import { listVehicles } from '../services/vehicles';
import { formatNumber, formatPercent, formatSpeed, formatDistance, rateTextClass } from '../lib/formatters';
import type { PaginatedResponse } from '../types/common';
import type { VehicleSummaryRow } from '../types/vehicle';

export default function VehicleList() {
  const navigate = useNavigate();
  const { page, setPage, limit, search, onSearch, sortBy, sortOrder, onSort } = usePagination('total_trips', 'desc');
  const { data, loading } = useApi<PaginatedResponse<VehicleSummaryRow>>(
    () => listVehicles({ page, limit, search, sort_by: sortBy, sort_order: sortOrder }),
    [page, limit, search, sortBy, sortOrder]
  );

  const columns = [
    { key: 'asset_id', label: 'Asset ID' },
    { key: 'asset_type', label: 'Type' },
    { key: 'total_trips', label: 'Total Trips', sortable: true, render: (r: VehicleSummaryRow) => formatNumber(r.total_trips) },
    { key: 'drivers_used', label: 'Drivers', render: (r: VehicleSummaryRow) => formatNumber(r.drivers_used) },
    { key: 'avg_speed_kmph', label: 'Avg Speed', sortable: true, render: (r: VehicleSummaryRow) => formatSpeed(r.avg_speed_kmph) },
    { key: 'total_distance_km', label: 'Total Distance', sortable: true, render: (r: VehicleSummaryRow) => formatDistance(r.total_distance_km) },
    { key: 'eta_success_rate', label: 'ETA Rate', sortable: true, render: (r: VehicleSummaryRow) => (
      <span className={rateTextClass(r.eta_success_rate)}>
        {formatPercent(r.eta_success_rate)}
      </span>
    )},
  ];

  return (
    <PageContainer>
      <div className="mb-4 w-72">
        <SearchInput value={search} onChange={onSearch} placeholder="Search vehicles..." />
      </div>
      <DataTable
        columns={columns}
        data={data?.data || []}
        loading={loading}
        sortBy={sortBy}
        sortOrder={sortOrder}
        onSort={onSort}
        onRowClick={(r) => navigate(`/vehicles/${r.vehicle_id}`)}
      />
      {data && <Pagination page={data.page} totalPages={data.total_pages} onPageChange={setPage} />}
    </PageContainer>
  );
}
