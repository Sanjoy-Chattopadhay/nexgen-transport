import { useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Database, UploadCloud, CheckCircle, XCircle, Satellite, MapPin, Activity,
  Clock, Target, Route as RouteIcon, GitCompareArrows, Download, Timer,
} from 'lucide-react';
import PageContainer from '../components/layout/PageContainer';
import DataTable from '../components/ui/DataTable';
import Pagination from '../components/ui/Pagination';
import SearchInput from '../components/ui/SearchInput';
import Spinner from '../components/ui/Spinner';
import Badge from '../components/ui/Badge';
import KPICard from '../components/ui/KPICard';
import { useApi } from '../hooks/useApi';
import { backendApi } from '../services/api';
import { getTTAStatus, uploadTTAFile, createTTASchema } from '../services/tta';
import { formatNumber, formatDateTime, formatDuration, formatDistance, formatPercent } from '../lib/formatters';
import type { TTATripRow, TTAUploadResult } from '../types/tta';

const statusVariant = (s: string | null): 'success' | 'info' | 'warning' | 'neutral' => {
  const v = (s || '').toLowerCase();
  if (v.includes('close')) return 'success';
  if (v.includes('open') || v.includes('running') || v.includes('transit')) return 'info';
  if (v.includes('cancel') || v.includes('hold')) return 'warning';
  return 'neutral';
};

const listTrips = (page: number, search: string, status: string) =>
  backendApi.get<any>('/tta/trips', { params: { page, page_size: 25, search, status } });

export default function TTAData() {
  const navigate = useNavigate();
  const fileInput = useRef<HTMLInputElement>(null);
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState('');
  const [status, setStatus] = useState('');
  const [selected, setSelected] = useState<number[]>([]);
  const [uploading, setUploading] = useState(false);
  const [uploadResult, setUploadResult] = useState<{ ok: boolean; msg: string } | null>(null);

  const { data: tableStatus, refetch: refetchStatus } = useApi(() => getTTAStatus());
  const { data: trips, loading: tripsLoading, refetch: refetchTrips } = useApi(
    () => listTrips(page, search, status),
    [page, search, status],
  );

  const handleUpload = async (file: File) => {
    setUploading(true);
    setUploadResult(null);
    try {
      await createTTASchema();
      const res = await uploadTTAFile(file);
      const r: TTAUploadResult = res.data;
      setUploadResult({
        ok: true,
        msg: `${r.blocks_found} block(s) parsed — ${r.trips_upserted} trip(s), ${formatNumber(r.gps_inserted)} GPS points inserted (${formatNumber(r.gps_skipped)} duplicates skipped)`,
      });
      refetchStatus();
      refetchTrips();
    } catch (err: any) {
      setUploadResult({ ok: false, msg: err?.response?.data?.detail || err.message || 'Upload failed' });
    } finally {
      setUploading(false);
      if (fileInput.current) fileInput.current.value = '';
    }
  };

  const toggleSelect = (no: number, e: React.MouseEvent) => {
    e.stopPropagation();
    setSelected(prev => prev.includes(no) ? prev.filter(x => x !== no)
      : prev.length >= 4 ? prev : [...prev, no]);
  };

  const exportCsv = () => {
    if (!trips?.items?.length) return;
    const cols = ['i_trip_no', 's_asset_id', 's_driver_name', 's_cnr_name', 's_org_node_name',
      's_dest_node_name', 'dt_trip_start', 'dt_trip_ata', 'c_trip_status',
      's_delivery_status', 'd_distance_travelled_km', 'i_transit_time_min', 'gps_points'];
    const lines = [cols.join(',')].concat(
      (trips.items as any[]).map((r: any) =>
        cols.map(c => `"${String(r[c] ?? '').replace(/"/g, '""')}"`).join(',')),
    );
    const blob = new Blob([lines.join('\n')], { type: 'text/csv' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `trips_page${page}.csv`;
    a.click();
    URL.revokeObjectURL(a.href);
  };

  const k = trips?.kpis;

  const columns = [
    { key: '_sel', label: '', render: (r: TTATripRow) => (
      <input type="checkbox" checked={selected.includes(r.i_trip_no)}
        onClick={e => toggleSelect(r.i_trip_no, e as any)} readOnly
        className="accent-blue-600 cursor-pointer" title="Select for comparison" />
    ) },
    { key: 'i_trip_no', label: 'Trip No', render: (r: TTATripRow) => <span className="text-blue-400 font-medium">{r.i_trip_no}</span> },
    { key: 's_asset_id', label: 'Vehicle', render: (r: TTATripRow) => r.s_asset_id ?? '-' },
    { key: 's_driver_name', label: 'Driver', render: (r: TTATripRow) => r.s_driver_name ?? '-' },
    { key: 's_cnr_name', label: 'Consignor', render: (r: TTATripRow) => (
      <span>{r.s_cnr_name ?? '-'}{r.i_cnr_id != null && <span className="text-gray-500 text-xs ml-1">#{r.i_cnr_id}</span>}</span>
    ) },
    { key: 'route', label: 'Route', render: (r: TTATripRow) => (
      <span className="text-gray-300">{r.s_org_node_name ?? '?'} <span className="text-gray-600">→</span> {r.s_dest_node_name ?? '?'}</span>
    ) },
    { key: 'dt_trip_start', label: 'Start', render: (r: TTATripRow) => formatDateTime(r.dt_trip_start) },
    { key: 'c_trip_status', label: 'Status', render: (r: TTATripRow) => <Badge label={r.c_trip_status ?? 'Unknown'} variant={statusVariant(r.c_trip_status)} /> },
    { key: 's_delivery_status', label: 'Delivery', render: (r: TTATripRow) => r.s_delivery_status
      ? <Badge label={r.s_delivery_status} variant={r.s_delivery_status.toLowerCase().includes('on time') ? 'success' : 'danger'} />
      : <span className="text-gray-600">-</span> },
    { key: 'd_distance_travelled_km', label: 'Distance', render: (r: TTATripRow) => formatDistance(r.d_distance_travelled_km != null ? Number(r.d_distance_travelled_km) : null) },
    { key: 'i_transit_time_min', label: 'Transit', render: (r: TTATripRow) => formatDuration(r.i_transit_time_min) },
    { key: 'gps_points', label: 'GPS Pings', render: (r: TTATripRow) => (
      <span className={r.gps_points > 0 ? 'text-emerald-400 font-medium' : 'text-gray-600'}>{formatNumber(r.gps_points)}</span>
    ) },
  ];

  const totalPages = trips ? Math.max(1, Math.ceil(trips.total / trips.page_size)) : 1;

  return (
    <PageContainer>
      {/* KPI strip */}
      {k && (
        <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-8 gap-3 mb-6">
          <KPICard label="Total Trips" value={formatNumber(k.total_trips)} icon={MapPin} color="blue" />
          <KPICard label="Closed" value={formatNumber(k.closed_trips)} icon={CheckCircle} color="green" />
          <KPICard label="Active" value={formatNumber(k.active_trips)} icon={Activity} color="amber" />
          <KPICard label="On-Time %" value={formatPercent(k.ontime_pct)} icon={Target} color={k.ontime_pct >= 90 ? 'green' : k.ontime_pct >= 75 ? 'amber' : 'red'} />
          <KPICard label="Avg Transit" value={formatDuration(k.avg_transit_min)} icon={Clock} color="purple" />
          <KPICard label="Avg Detention" value={formatDuration(k.avg_detention_min)} icon={Timer} color="red" />
          <KPICard label="Total Distance" value={formatDistance(k.total_distance_km != null ? Number(k.total_distance_km) : null)} icon={RouteIcon} color="cyan" />
          <KPICard label="GPS Coverage" value={formatPercent(k.gps_coverage_pct)} icon={Satellite} color="blue" />
        </div>
      )}

      {/* Upload card */}
      <div className="bg-gray-900 rounded-xl border border-gray-800 p-5 mb-6">
        <div className="flex items-start justify-between gap-4 flex-wrap">
          <div className="flex items-center gap-3">
            <div className="w-9 h-9 bg-blue-600/20 rounded-lg flex items-center justify-center">
              <UploadCloud className="w-5 h-5 text-blue-400" />
            </div>
            <div>
              <h2 className="font-semibold text-gray-200">Upload Trip Export</h2>
              <p className="text-xs text-gray-500">TTA trip + trip-wise GPS file (.json / .txt) — trips, consignors, GPS and waypoint patterns store automatically</p>
            </div>
          </div>
          <div>
            <input ref={fileInput} type="file" accept=".json,.txt" className="hidden"
              onChange={e => { const f = e.target.files?.[0]; if (f) handleUpload(f); }} />
            <button onClick={() => fileInput.current?.click()} disabled={uploading}
              className="flex items-center gap-2 px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-lg text-sm font-medium disabled:opacity-50 transition-colors">
              {uploading ? <div className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" /> : <UploadCloud className="w-4 h-4" />}
              {uploading ? 'Ingesting…' : 'Choose File'}
            </button>
          </div>
        </div>
        {uploadResult && (
          <div className={`mt-3 flex items-start gap-2 text-xs rounded-lg p-2.5 ${uploadResult.ok ? 'bg-emerald-900/30 text-emerald-400' : 'bg-red-900/30 text-red-400'}`}>
            {uploadResult.ok ? <CheckCircle className="w-3.5 h-3.5 mt-0.5 shrink-0" /> : <XCircle className="w-3.5 h-3.5 mt-0.5 shrink-0" />}
            <span className="break-all">{uploadResult.msg}</span>
          </div>
        )}
      </div>

      {/* Trips list */}
      <div className="bg-gray-900 rounded-xl border border-gray-800 p-5 mb-6">
        <div className="flex items-center justify-between gap-3 mb-4 flex-wrap">
          <div className="flex items-center gap-2">
            <Satellite className="w-5 h-5 text-blue-400" />
            <h2 className="text-lg font-semibold text-white">All Trips {trips && <span className="text-sm text-gray-500 font-normal">({formatNumber(trips.total)})</span>}</h2>
          </div>
          <div className="flex items-center gap-2 flex-wrap">
            <select value={status} onChange={e => { setStatus(e.target.value); setPage(1); }}
              className="bg-gray-800 border border-gray-700 rounded-lg text-sm text-gray-200 px-3 py-2 focus:outline-none focus:border-blue-500">
              <option value="">All statuses</option>
              {(trips?.statuses ?? []).map((s: string) => <option key={s} value={s}>{s}</option>)}
            </select>
            <div className="w-64">
              <SearchInput value={search} onChange={(v: string) => { setSearch(v); setPage(1); }} placeholder="Search trip / vehicle / driver…" />
            </div>
            <button onClick={exportCsv} title="Export current page as CSV"
              className="flex items-center gap-1.5 px-3 py-2 bg-gray-800 hover:bg-gray-700 border border-gray-700 text-gray-300 rounded-lg text-xs font-medium transition-colors">
              <Download className="w-3.5 h-3.5" /> CSV
            </button>
            <button onClick={() => navigate(`/tta-compare?trips=${selected.join(',')}`)} disabled={selected.length < 2}
              title="Tick 2–4 trips to compare"
              className="flex items-center gap-1.5 px-3 py-2 bg-purple-600 hover:bg-purple-700 text-white rounded-lg text-xs font-medium disabled:opacity-40 transition-colors">
              <GitCompareArrows className="w-3.5 h-3.5" /> Compare {selected.length > 0 && `(${selected.length})`}
            </button>
          </div>
        </div>
        <DataTable<TTATripRow>
          columns={columns}
          data={trips?.items ?? []}
          loading={tripsLoading}
          onRowClick={row => navigate(`/trips/${row.i_trip_no}`)}
          emptyMessage="No trips stored yet — upload an export file above"
        />
        <Pagination page={page} totalPages={totalPages} onPageChange={setPage} />
      </div>

      {/* Storage status */}
      <div className="bg-gray-900 rounded-xl border border-gray-800 p-5">
        <div className="flex items-center gap-2 mb-4">
          <Database className="w-5 h-5 text-blue-400" />
          <h2 className="text-lg font-semibold text-white">Storage Status</h2>
        </div>
        {tableStatus ? (
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            {Object.entries(tableStatus).map(([table, count]) => (
              <div key={table} className="bg-gray-800/50 rounded-lg p-3">
                <p className="text-xs text-gray-500 mb-1">{table}</p>
                <p className="text-lg font-bold text-gray-200">{count === -1 ? 'missing' : formatNumber(count)}</p>
              </div>
            ))}
          </div>
        ) : <Spinner />}
      </div>
    </PageContainer>
  );
}
