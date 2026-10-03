import { useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { Route as RouteIcon, Trophy, PackageX, Search, Download } from 'lucide-react';
import PageContainer from '../../components/layout/PageContainer';
import Spinner from '../../components/ui/Spinner';
import ChartCard from '../../components/ui/ChartCard';
import DataTable from '../../components/ui/DataTable';
import HBarChart from '../../components/charts/HBarChart';
import { useApi } from '../../hooks/useApi';
import { useTTAFilters } from '../../components/tta/dashboard/FilterContext';
import { getBestByLane, type LaneChoice, type LaneCarrier } from '../../services/transporters';
import { formatNumber } from '../../lib/formatters';
import { downloadCsv } from '../../lib/csv';
import { tc } from '../../../../core/theme';

const otd = (v: number | null) => v == null ? '—' : (
  <span className={v >= 95 ? 'text-emerald-400 font-semibold' : v >= 80 ? 'text-amber-400' : 'text-red-400 font-semibold'}>
    {v}%
  </span>
);

export default function BestByLane() {
  const { params, paramsKey } = useTTAFilters();
  const [minTrips, setMinTrips] = useState(5);
  const [search, setSearch] = useState('');
  const [open, setOpen] = useState<string | null>(null);

  const { data, loading } = useApi(
    () => getBestByLane(params as never, minTrips), [paramsKey, minTrips]);

  const lanes = useMemo(
    () => (data?.lanes ?? []).filter(l => !search
      || l.lane.toLowerCase().includes(search.toLowerCase())
      || l.best.transporter.toLowerCase().includes(search.toLowerCase())),
    [data, search]);

  // Where switching carrier is worth the most on-time points.
  const biggestWins = useMemo(
    () => lanes.slice(0, 12).map(l => ({
      lane: l.lane.replace(' → ', ' → '), gap: l.otd_gap_pts ?? 0, trips: l.trips,
    })), [lanes]);

  return (
    <PageContainer title="🏁 Best carrier per lane">
      <p className="text-sm text-gray-400 -mt-4 mb-6 max-w-3xl leading-relaxed">
        Carriers judged only against others who ran the <strong className="text-gray-200">same
        lane</strong>. Comparing them on overall averages mostly measures who holds the longer
        routes; this isolates the carrier from the route, which is the only comparison that can
        tell you who to give a lane to.
      </p>

      <div className="flex items-center gap-4 flex-wrap mb-6">
        <label className="flex items-center gap-2 text-xs text-gray-400">
          Minimum trips on the lane
          <input type="range" min={2} max={20} value={minTrips}
            onChange={e => setMinTrips(Number(e.target.value))} className="accent-blue-600" />
          <span className="text-blue-400 font-semibold w-5">{minTrips}</span>
        </label>
        <div className="flex items-center gap-2 bg-gray-900 border border-gray-800 rounded-lg px-3 py-1.5">
          <Search className="w-3.5 h-3.5 text-gray-500" />
          <input value={search} onChange={e => setSearch(e.target.value)}
            placeholder="lane or carrier…"
            className="bg-transparent text-xs text-gray-300 outline-none w-48" />
        </div>
        {data && (
          <button onClick={() => downloadCsv(
            data.lanes.map(l => ({
              lane: l.lane, carriers: l.carriers, trips: l.trips,
              best: l.best.transporter, best_otd: l.best.otd_pct, best_trips: l.best.trips,
              worst: l.worst.transporter, worst_otd: l.worst.otd_pct, gap_pts: l.otd_gap_pts,
            })), 'best-carrier-per-lane.csv')}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs text-gray-300 border border-gray-700 bg-gray-800 hover:text-white">
            <Download className="w-3.5 h-3.5" /> Export CSV
          </button>
        )}
      </div>

      {/* Commodity was asked for and cannot be built. Say so here, not in a doc. */}
      {data?.commodity && !data.commodity.available && (
        <div className="flex gap-3 items-start bg-amber-950/25 border border-amber-900/60 rounded-xl p-4 mb-6">
          <PackageX className="w-5 h-5 text-amber-400 shrink-0 mt-0.5" />
          <div className="text-xs text-amber-200/90 leading-relaxed">
            <p className="font-semibold mb-0.5">Best carrier by commodity is not available.</p>
            <p>{data.commodity.reason}</p>
            <p className="text-amber-300/70 mt-1">To enable it: {data.commodity.needed}</p>
          </div>
        </div>
      )}

      {loading ? <Spinner /> : data && (
        <>
          <ChartCard title="Where switching carrier is worth the most" icon={Trophy}
            iconColor="text-amber-400" className="mb-6"
            explain="On-time points between the best and worst carrier on the same lane. A long bar is a service win available without renegotiating anything — the lane already has a carrier doing it better."
            method={{
              formula: "Per lane, best carrier's on-time rate minus worst carrier's, over carriers with at least the minimum trips on that lane. Ranked by that gap.",
              plot: "hbar",
              caveat: "A gap is an opportunity, not a promise. The weaker carrier may be running the harder loads on that lane — check trips and distance before moving volume.",
            }}>
            <HBarChart data={biggestWins} nameKey="lane" valueKey="gap"
              valueLabel="on-time points" color={tc(tc('#f59e0b'))} />
          </ChartCard>

          <ChartCard title="Every lane with a real choice" icon={RouteIcon} iconColor="text-blue-400"
            explain={`${data.total_lanes} lanes have two or more carriers with at least ${data.min_trips} trips each. Click a row to see every carrier on that lane.`}
            method={{
              formula: "Carriers grouped by lane and ranked on the Wilson LOWER bound of their on-time rate, then their own schedule adherence, then transit hours. The lower bound is what stops a carrier winning a lane on three lucky trips.",
              plot: "table",
              caveat: "Lanes where only one carrier qualifies are excluded — there is no choice to make, so ranking would be theatre.",
            }}>
            <div className="max-h-[560px] overflow-y-auto">
              <DataTable
                onRowClick={(r: LaneChoice) => setOpen(open === r.lane ? null : r.lane)}
                columns={[
                  { key: 'lane', label: 'Lane',
                    render: (r: LaneChoice) => <span className="text-gray-200">{r.lane}</span> },
                  { key: 'carriers', label: 'Carriers' },
                  { key: 'trips', label: 'Trips' },
                  { key: 'best', label: 'Give it to',
                    render: (r: LaneChoice) => (
                      <Link to={`/transporters/${encodeURIComponent(r.best.transporter)}`}
                        onClick={e => e.stopPropagation()}
                        className="text-emerald-400 hover:text-emerald-300">
                        {r.best.transporter}
                      </Link>) },
                  { key: 'best_otd', label: 'Its OTD',
                    render: (r: LaneChoice) => (
                      <span>{otd(r.best.otd_pct)}
                        <span className="text-gray-600 text-xs"> ({r.best.trips} trips)</span>
                      </span>) },
                  { key: 'worst', label: 'Weakest',
                    render: (r: LaneChoice) => (
                      <span className="text-gray-400">{r.worst.transporter}</span>) },
                  { key: 'otd_gap_pts', label: 'Gap',
                    render: (r: LaneChoice) => (
                      <span className={(r.otd_gap_pts ?? 0) >= 20 ? 'text-red-400 font-semibold' : 'text-gray-300'}>
                        {r.otd_gap_pts} pts
                      </span>) },
                  { key: 'avg_distance_km', label: 'Avg km' },
                ]} data={lanes} emptyMessage="No lane has two carriers with enough trips to compare." />
            </div>
          </ChartCard>

          {open && (() => {
            const lane = lanes.find(l => l.lane === open);
            if (!lane) return null;
            return (
              <ChartCard title={`Every carrier on ${lane.lane}`} icon={RouteIcon}
                iconColor="text-cyan-400" className="mt-6"
                explain="The full field for this lane, best first. The 95% band is what the on-time rate could actually be given how few trips some of these rest on."
                method={{
                  formula: "This lane's trips only, grouped by carrier. 'Vs plan' is that carrier's mean actual transit minus its mean quoted transit ON THIS LANE.",
                  plot: "table",
                  caveat: "Two carriers whose confidence bands overlap have not been shown to differ, however far apart their point estimates look.",
                }}
                actions={
                  <button onClick={() => setOpen(null)}
                    className="text-xs text-gray-500 hover:text-gray-300">✕ close</button>
                }>
                <DataTable columns={[
                  { key: 'transporter', label: 'Carrier',
                    render: (r: LaneCarrier) => (
                      <Link to={`/transporters/${encodeURIComponent(r.transporter)}`}
                        className="text-blue-400 hover:text-blue-300">{r.transporter}</Link>) },
                  { key: 'trips', label: 'Trips' },
                  { key: 'otd_pct', label: 'OTD %', render: (r: LaneCarrier) => otd(r.otd_pct) },
                  { key: 'otd_ci_low', label: '95% band',
                    render: (r: LaneCarrier) => r.otd_ci_low == null ? '—'
                      : <span className="text-gray-500 text-xs">{r.otd_ci_low}–{r.otd_ci_high}%</span> },
                  { key: 'avg_transit_hours', label: 'Transit',
                    render: (r: LaneCarrier) => r.avg_transit_hours != null ? `${r.avg_transit_hours} h` : '—' },
                  { key: 'schedule_variance_hours', label: 'Vs plan',
                    render: (r: LaneCarrier) => r.schedule_variance_hours == null ? '—' : (
                      <span className={r.schedule_variance_hours > 0 ? 'text-red-400' : 'text-emerald-400'}>
                        {r.schedule_variance_hours > 0 ? '+' : ''}{r.schedule_variance_hours} h
                      </span>) },
                  { key: 'avg_detention_hours', label: 'Detention',
                    render: (r: LaneCarrier) => r.avg_detention_hours != null ? `${r.avg_detention_hours} h` : '—' },
                ]} data={lane.all} />
              </ChartCard>
            );
          })()}

          <p className="text-xs text-gray-500 mt-4">
            {formatNumber(data.total_lanes)} comparable lanes at a {data.min_trips}-trip minimum.
          </p>
        </>
      )}
    </PageContainer>
  );
}
