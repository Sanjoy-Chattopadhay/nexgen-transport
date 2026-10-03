import { useNavigate } from 'react-router-dom';
import { MapPin, CheckCircle, Clock, Gauge, Target, Activity } from 'lucide-react';
import PageContainer from '../components/layout/PageContainer';
import KPICard from '../components/ui/KPICard';
import SearchInput from '../components/ui/SearchInput';
import DataTable from '../components/ui/DataTable';
import Pagination from '../components/ui/Pagination';
import Badge from '../components/ui/Badge';
import { useApi } from '../hooks/useApi';
import { usePagination } from '../hooks/usePagination';
import { listTrips, getTripStats } from '../services/trips';
import { formatNumber, formatPercent, formatSpeed, formatDuration, formatDateTime } from '../lib/formatters';
import type { PaginatedResponse } from '../types/common';
import type { TripRow, TripStats } from '../types/trip';

export default function TripList() {
  const navigate = useNavigate();
  const { page, setPage, limit, search, onSearch, sortBy, sortOrder, onSort } = usePagination('trip_start', 'desc');
  const { data: stats } = useApi<TripStats>(() => getTripStats());
  const { data, loading } = useApi<PaginatedResponse<TripRow>>(
    () => listTrips({ page, limit, search, sort_by: sortBy, sort_order: sortOrder }),
    [page, limit, search, sortBy, sortOrder]
  );

  const etaColor = stats?.eta_success_rate != null ? (stats.eta_success_rate >= 90 ? 'green' : stats.eta_success_rate >= 80 ? 'amber' : 'red') : 'red';

  const columns = [
    { key: 'dispatch_entry_no', label: 'Dispatch#' },
    { key: 'driver_name', label: 'Driver' },
    { key: 'asset_id', label: 'Vehicle' },
    { key: 'origin_name', label: 'Origin' },
    { key: 'destination_name', label: 'Destination' },
    { key: 'trip_start', label: 'Start', render: (r: TripRow) => formatDateTime(r.trip_start) },
    { key: 'trip_duration_minutes', label: 'Duration', sortable: true, render: (r: TripRow) => formatDuration(r.trip_duration_minutes) },
    { key: 'eta_met', label: 'ETA Met', render: (r: TripRow) => <Badge label={r.eta_met ? 'Yes' : 'No'} variant={r.eta_met ? 'success' : 'danger'} /> },
    { key: 'avg_speed_kmph', label: 'Speed', sortable: true, render: (r: TripRow) => formatSpeed(r.avg_speed_kmph) },
    { key: 'trip_status', label: 'Status' },
  ];

  return (
    <PageContainer>
      {stats && (
        <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-4 mb-6">
          <KPICard label="Total Trips" value={formatNumber(stats.total_trips)} icon={MapPin} color="blue" />
          <KPICard label="Completed" value={formatNumber(stats.completed_trips)} icon={CheckCircle} color="green" />
          <KPICard label="Active" value={formatNumber(stats.active_trips)} icon={Activity} color="amber" />
          <KPICard label="Avg Duration" value={formatDuration(stats.avg_duration_minutes)} icon={Clock} color="purple" />
          <KPICard label="Avg Speed" value={formatSpeed(stats.avg_speed_kmph)} icon={Gauge} color="cyan" />
          <KPICard label="ETA Rate" value={formatPercent(stats.eta_success_rate)} icon={Target} color={etaColor} />
        </div>
      )}
      <div className="mb-4 w-72">
        <SearchInput value={search} onChange={onSearch} placeholder="Search trips..." />
      </div>
      <DataTable
        columns={columns}
        data={data?.data || []}
        loading={loading}
        sortBy={sortBy}
        sortOrder={sortOrder}
        onSort={onSort}
        onRowClick={(r) => navigate(`/trips/${r.id}`)}
      />
      {data && <Pagination page={data.page} totalPages={data.total_pages} onPageChange={setPage} />}
    </PageContainer>
  );
}
