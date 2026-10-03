import { useState, useRef, useEffect } from 'react';
import { MapPin, RefreshCw, Clock, Flame, Landmark } from 'lucide-react';
import PageContainer from '../components/layout/PageContainer';
import Spinner from '../components/ui/Spinner';
import DataTable from '../components/ui/DataTable';
import SearchInput from '../components/ui/SearchInput';
import Pagination from '../components/ui/Pagination';
import KPICard from '../components/ui/KPICard';
import WaypointReport from '../components/tta/WaypointReport';
import { useApi } from '../hooks/useApi';
import { backendApi } from '../services/api';
import { KPI_INFO } from '../lib/kpiInfo';
import { formatNumber, formatDuration, formatDateTime } from '../lib/formatters';

const getWaypoints = (page: number, search: string, sort: string) =>
  backendApi.get<any>('/tta/waypoints', { params: { page, page_size: 25, search, sort } });
const getWaypointDetail = (id: number) => backendApi.get<any>(`/tta/waypoints/${id}`);
const refreshRegistry = () => backendApi.post<any>('/tta/waypoints/refresh', null, { timeout: 300000 });

export default function TTAWaypoints() {
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState('');
  const [sort, setSort] = useState('stopped_min');
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  const { data, loading, refetch } = useApi(() => getWaypoints(page, search, sort), [page, search, sort]);
  const { data: detail, loading: detailLoading } = useApi(
    () => selectedId ? getWaypointDetail(selectedId) : Promise.resolve({ data: null }),
    [selectedId],
  );

  const doRefresh = async () => {
    setRefreshing(true);
    try { await refreshRegistry(); refetch(); } finally { setRefreshing(false); }
  };

  const onSort = (col: string) => { setSort(col); setPage(1); };
  const totalPages = data ? Math.max(1, Math.ceil(data.total / data.page_size)) : 1;
  const wp = detail?.waypoint;

  // The detail panel renders below a 25-row table, so it opens off-screen.
  // Flash a highlight the moment a waypoint is picked...
  const detailRef = useRef<HTMLDivElement>(null);
  const [justOpened, setJustOpened] = useState(false);
  useEffect(() => {
    if (!selectedId) return;
    setJustOpened(true);
    const t = setTimeout(() => setJustOpened(false), 1400);
    return () => clearTimeout(t);
  }, [selectedId]);

  // ...but only scroll once the report has finished loading, so we scroll to the
  // full-height panel (scrolling during the loading spinner gets cancelled by the
  // reflow when the report renders in).
  useEffect(() => {
    if (selectedId && !detailLoading && wp) {
      detailRef.current?.scrollIntoView({ block: 'start' });
    }
  }, [selectedId, detailLoading, wp]);

  return (
    <PageContainer>
      {/* KPIs */}
      {data?.kpis && (
        <div className="grid grid-cols-2 md:grid-cols-5 gap-4 mb-6">
          <KPICard label="Waypoints Registered" value={formatNumber(data.kpis.waypoints)} icon={MapPin} color="blue" info={KPI_INFO.waypointsRegistered} />
          <KPICard label="Stop Events Stored" value={formatNumber(data.kpis.total_stop_events)} icon={Flame} color="amber" info={KPI_INFO.stopEvents} />
          <KPICard label="Total Standstill" value={formatDuration(data.kpis.total_stopped_min)} icon={Clock} color="purple" info={KPI_INFO.totalStandstill} />
          <KPICard label="Longest Single Stop" value={formatDuration(data.kpis.longest_stop_min)} icon={Clock} color="red" info={KPI_INFO.longestStop} />
          <KPICard label="Worst Waypoint" value={data.kpis.worst_waypoint ? (data.kpis.worst_waypoint.length > 14 ? data.kpis.worst_waypoint.slice(0, 14) + '…' : data.kpis.worst_waypoint) : '-'} icon={Landmark} color="cyan" info={KPI_INFO.worstWaypoint} />
        </div>
      )}

      {/* Registry table */}
      <div className="bg-gray-900 rounded-xl border border-gray-800 p-5 mb-6">
        <div className="flex items-center justify-between gap-4 mb-1 flex-wrap">
          <h2 className="text-lg font-semibold text-white flex items-center gap-2">
            <MapPin className="w-5 h-5 text-blue-400" /> Waypoint Registry
            {data && <span className="text-sm text-gray-500 font-normal">({formatNumber(data.total)})</span>}
          </h2>
          <div className="flex items-center gap-3">
            <div className="w-64">
              <SearchInput value={search} onChange={(v: string) => { setSearch(v); setPage(1); }} placeholder="Search waypoint / state…" />
            </div>
            <button onClick={doRefresh} disabled={refreshing}
              className="flex items-center gap-2 px-3 py-2 bg-gray-800 hover:bg-gray-700 border border-gray-700 text-gray-300 rounded-lg text-xs font-medium disabled:opacity-50 transition-colors">
              <RefreshCw className={`w-3.5 h-3.5 ${refreshing ? 'animate-spin' : ''}`} /> Rebuild patterns
            </button>
          </div>
        </div>
        <p className="text-xs text-gray-500 mb-4">
          Every waypoint ever seen in GPS data is stored here with its accumulated behaviour pattern — new waypoints
          register automatically on every upload. These stored patterns feed route analysis, driver analysis and ETA models.
          <span className="text-cyan-400 font-medium"> Click any row to open its full analysis report ↓</span>
        </p>
        <DataTable
          columns={[
            { key: 's_wpnt', label: 'Waypoint', sortable: true, render: (r: any) => (
              <span className="text-gray-200">{r.s_wpnt} <span className="text-gray-500 text-xs">({r.s_state})</span></span>
            ) },
            { key: 'stopped_min', label: 'Standstill', sortable: true, render: (r: any) => (
              <span className="text-amber-400 font-semibold">{formatDuration(r.stopped_min)}</span>
            ) },
            { key: 'stop_events', label: 'Stops', sortable: true },
            { key: 'longest_stop_min', label: 'Longest', sortable: true, render: (r: any) => formatDuration(r.longest_stop_min) },
            { key: 'total_trips', label: 'Trips', sortable: true },
            { key: 'total_vehicles', label: 'Vehicles' },
            { key: 'total_pings', label: 'Pings', sortable: true, render: (r: any) => formatNumber(r.total_pings) },
            { key: 'last_seen', label: 'Last Seen', sortable: true, render: (r: any) => formatDateTime(r.last_seen) },
          ]}
          data={data?.items ?? []}
          loading={loading}
          sortBy={sort}
          sortOrder="desc"
          onSort={onSort}
          onRowClick={(r: any) => setSelectedId(r.id)}
          emptyMessage="No waypoints yet — upload TTA GPS data"
        />
        <Pagination page={page} totalPages={totalPages} onPageChange={setPage} />
      </div>

      {/* Drill-down */}
      {selectedId && (
        <div ref={detailRef} className={`bg-gray-900 rounded-xl border p-5 scroll-mt-4 transition-all duration-500 ${justOpened ? 'border-cyan-500 ring-2 ring-cyan-500/40' : 'border-blue-900/60'}`}>
          {detailLoading ? <Spinner /> : wp && (
            <>
              <div className="flex items-start justify-between flex-wrap gap-3 mb-4">
                <div>
                  <h2 className="text-lg font-semibold text-white flex items-center gap-2">
                    <Landmark className="w-5 h-5 text-cyan-400" /> {wp.s_wpnt}
                    <span className="text-sm text-gray-500 font-normal">({wp.s_state})</span>
                  </h2>
                  <p className="text-xs text-gray-500 mt-1">
                    First seen {formatDateTime(wp.first_seen)} · last seen {formatDateTime(wp.last_seen)} · pattern refreshed {formatDateTime(wp.last_refreshed)}
                  </p>
                </div>
                <button onClick={() => setSelectedId(null)} className="text-xs text-gray-500 hover:text-gray-300">✕ close</button>
              </div>

              <WaypointReport wp={wp} visits={detail?.visits ?? []} />
            </>
          )}
        </div>
      )}
    </PageContainer>
  );
}
