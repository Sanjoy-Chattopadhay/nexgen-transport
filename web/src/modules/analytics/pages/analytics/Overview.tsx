import { useMemo } from 'react';
import { Link } from 'react-router-dom';
import {
  Factory, Truck, Clock, Route as RouteIcon, Users, Gauge as GaugeIcon,
  Timer, AlertTriangle, PieChart as PieIcon, MapPin, TrendingUp, Filter,
  ArrowRight, Siren, Map as MapIcon,
} from 'lucide-react';
import PageContainer from '../../components/layout/PageContainer';
import Spinner from '../../components/ui/Spinner';
import KPICard from '../../components/ui/KPICard';
import ChartCard from '../../components/ui/ChartCard';
import DonutChart from '../../components/charts/DonutChart';
import GaugeChart from '../../components/charts/GaugeChart';
import DualAxisChart from '../../components/charts/DualAxisChart';
import FunnelStages from '../../components/charts/FunnelStages';
import HBarChart from '../../components/charts/HBarChart';
import { useApi } from '../../hooks/useApi';
import { useTTAFilters } from '../../components/tta/dashboard/FilterContext';
import { useDrillDown } from '../../context/DrillDownContext';
import InfoDot from '../../components/ui/InfoDot';
import { KPI_INFO } from '../../lib/kpiInfo';
import DataTable from '../../components/ui/DataTable';
import {
  getDashKpis, getDashTimeseries, getDashGroup, getDashFunnel, getDashRecords,
  getDashGeoStates, type GroupRow, type StateRow,
} from '../../services/ttaDashboard';
import { getReliabilityMatrix } from '../../services/transporters';
import { formatNumber, formatDateTime } from '../../lib/formatters';
import type { FigureMethod } from '../../lib/figureNotes';
import { tc } from '../../../../core/theme';

const OTD_TARGET = 95;

/** A carrier or lane needs this many trips before a bad rate is a finding
 *  rather than a coincidence. Applies to every table on this page. */
const MIN_TRIPS = 10;

type Severity = 'critical' | 'warning' | 'info';

interface Finding {
  severity: Severity;
  title: string;
  detail: string;
  to: string;
  cta: string;
}

const SEVERITY_STYLE: Record<Severity, string> = {
  critical: 'border-red-900 bg-red-950/30',
  warning: 'border-amber-900 bg-amber-950/25',
  info: 'border-gray-800 bg-gray-900',
};

const SEVERITY_DOT: Record<Severity, string> = {
  critical: 'bg-red-500', warning: 'bg-amber-500', info: 'bg-blue-500',
};

/** A compact worst-first table that says where the rest of it lives. */
function DeepDive({ title, icon, iconColor, explain, to, linkLabel, columns, rows, empty, method }: {
  title: string; icon: typeof Truck; iconColor: string; explain: string;
  to: string; linkLabel: string; columns: any[]; rows: any[]; empty: string;
  method?: FigureMethod;
}) {
  return (
    <ChartCard title={title} icon={icon} iconColor={iconColor} explain={explain} method={method}
      actions={
        <Link to={to} className="flex items-center gap-1 text-xs text-blue-400 hover:text-blue-300 whitespace-nowrap">
          {linkLabel} <ArrowRight className="w-3.5 h-3.5" />
        </Link>
      }>
      <DataTable columns={columns} data={rows} emptyMessage={empty} />
    </ChartCard>
  );
}

const hrs = (v: number | null | undefined) => (v == null ? '—' : `${v.toFixed(1)} h`);

const otdCell = (v: number | null | undefined) => v == null ? '\u2014' : (
  <span className={v >= 95 ? 'text-emerald-400' : v >= 80 ? 'text-amber-400' : 'text-red-400 font-semibold'}>
    {v}%
  </span>
);

export default function Overview() {
  const { params, paramsKey } = useTTAFilters();
  const { open } = useDrillDown();

  const drillTrips = () => open({
    title: 'Trips in current filter',
    subtitle: 'Most recent first — click a row to open the trip',
    columns: [
      { key: 'trip_id', label: 'Trip' },
      { key: 'transporter', label: 'Transporter' },
      { key: 'destination', label: 'Destination' },
      { key: 'dept_dt', label: 'Departed', render: (r: any) => formatDateTime(r.dept_dt) },
      { key: 'distance_km', label: 'Km', align: 'right' },
    ],
    rowLink: (r: any) => r.trip_id ? `/trips/${r.trip_id}` : null,
    load: () => getDashRecords(500, params).then(res => res.data.rows),
    empty: 'No trips match the current filter.',
  });

  const { data: kpi, loading: kpiLoading } = useApi(() => getDashKpis(params), [paramsKey]);
  const { data: ts } = useApi(() => getDashTimeseries('D', params), [paramsKey]);
  const { data: dest } = useApi(() => getDashGroup('destination', 1, params), [paramsKey]);
  const { data: trans } = useApi(() => getDashGroup('transporter', 1, params), [paramsKey]);
  const { data: fun } = useApi(() => getDashFunnel(params), [paramsKey]);
  const { data: states } = useApi(() => getDashGeoStates(params), [paramsKey]);
  const { data: matrix } = useApi(
    () => getReliabilityMatrix(params as never, MIN_TRIPS), [paramsKey]);

  const cur = kpi?.current ?? {};

  // ---- deep-dive slices, each ranked so the worst row is the first row -----
  const worstCarriers = useMemo(
    () => (trans?.rows ?? [])
      .filter(r => r.trips >= MIN_TRIPS && r.otd_pct != null)
      .sort((a, b) => (a.otd_pct ?? 0) - (b.otd_pct ?? 0)).slice(0, 6),
    [trans]);

  // Ranked by how far behind its OWN promised ETA a lane runs, not by transit
  // time: a long lane is not a problem, a lane that misses its own quote is.
  const slippingLanes = useMemo(
    () => (dest?.rows ?? [])
      .filter(r => r.trips >= MIN_TRIPS && r.schedule_variance_hours != null)
      .sort((a, b) => (b.schedule_variance_hours ?? 0) - (a.schedule_variance_hours ?? 0))
      .slice(0, 6),
    [dest]);

  const detentionLanes = useMemo(
    () => (dest?.rows ?? [])
      .filter(r => r.trips >= MIN_TRIPS && r.avg_detention_hours != null)
      .sort((a, b) => (b.avg_detention_hours ?? 0) - (a.avg_detention_hours ?? 0))
      .slice(0, 6),
    [dest]);

  const topStates = useMemo<StateRow[]>(
    () => (states?.states ?? []).slice(0, 6), [states]);

  // ---- findings: what to act on, ordered by the cost of ignoring it --------
  const findings = useMemo<Finding[]>(() => {
    const out: Finding[] = [];
    const critical = matrix?.quadrants?.critical;
    const grow = matrix?.quadrants?.grow;

    if (critical && critical.carriers > 0 && (critical.share_pct ?? 0) >= 5) {
      out.push({
        severity: 'critical',
        title: critical.carriers + ' carrier' + (critical.carriers > 1 ? 's' : '')
          + ' carry ' + critical.share_pct + '% of freight and miss dates',
        detail: 'Median on-time ' + (critical.median_otd_pct ?? '-') + '% across '
          + formatNumber(critical.trips) + ' trips. This is the most expensive combination '
          + 'on the panel: volume already committed to service that does not land.',
        to: '/transporters/league', cta: 'Open the reliability matrix',
      });
    }

    if (grow && grow.carriers > 0 && (critical?.carriers ?? 0) > 0) {
      out.push({
        severity: 'info',
        title: grow.carriers + ' carriers deliver but are barely used',
        detail: 'They hold only ' + grow.share_pct + '% of freight at a median '
          + (grow.median_otd_pct ?? '-') + '% on-time. Moving volume here from the critical '
          + 'list is the cheapest service improvement available - no new contract, no new rate.',
        to: '/transporters/league', cta: 'See who they are',
      });
    }

    if (cur.otd_pct != null && cur.otd_pct < OTD_TARGET) {
      const gap = Math.round((OTD_TARGET - cur.otd_pct) * 10) / 10;
      const missed = cur.trips ? Math.round((gap / 100) * cur.trips) : null;
      out.push({
        severity: cur.otd_pct < 80 ? 'critical' : 'warning',
        title: 'On-time delivery is ' + gap + ' points below the ' + OTD_TARGET + '% target',
        detail: missed
          ? 'Closing the gap is about ' + formatNumber(missed) + ' trips over this window '
            + 'that arrived late and did not have to.'
          : 'Every point below target is a broken customer promise.',
        to: '/analytics/trends', cta: 'See the trend',
      });
    }

    const worstState = (states?.states ?? [])
      .filter(x => x.judged_trips >= MIN_TRIPS && x.otd_pct != null)
      .sort((a, b) => (a.otd_pct ?? 0) - (b.otd_pct ?? 0))[0];
    if (worstState && (worstState.otd_pct ?? 100) < OTD_TARGET) {
      out.push({
        severity: (worstState.otd_pct ?? 100) < 80 ? 'critical' : 'warning',
        title: worstState.state + ' is the worst-served region at ' + worstState.otd_pct + '% on-time',
        detail: formatNumber(worstState.trips) + ' trips (' + worstState.share_pct
          + '% of freight), average transit ' + (worstState.avg_transit_hours ?? '-')
          + ' h across ' + worstState.destinations + ' destinations. Check lane difficulty '
          + 'before changing carrier.',
        to: '/analytics/geo', cta: 'Open the region map',
      });
    }

    if (cur.avg_detention_hours != null && cur.avg_detention_hours > 4) {
      out.push({
        severity: cur.avg_detention_hours > 8 ? 'critical' : 'warning',
        title: 'Trucks wait ' + cur.avg_detention_hours + ' h at the plant on average',
        detail: 'Detention is time you pay for twice - once in demurrage and again in the '
          + 'capacity it takes off the road. It is a loading-bay problem before it is a '
          + 'carrier problem.',
        to: '/analytics/distributions', cta: 'See the spread',
      });
    }

    if (cur.avg_gps_uptime != null && cur.avg_gps_uptime < 90) {
      out.push({
        severity: cur.avg_gps_uptime < 75 ? 'critical' : 'warning',
        title: 'GPS uptime is ' + cur.avg_gps_uptime + '% - part of the fleet is unmeasured',
        detail: 'Every service and safety number on this dashboard rests on the GPS trail. '
          + 'Where tracking is dark a carrier is not proven good, only unproven.',
        to: '/analytics/safety', cta: 'Check tracking quality',
      });
    }

    const rows = trans?.rows ?? [];
    const totalTrips = rows.reduce((a, r) => a + r.trips, 0);
    const top3 = [...rows].sort((a, b) => b.trips - a.trips).slice(0, 3);
    const top3Share = totalTrips
      ? Math.round((100 * top3.reduce((a, r) => a + r.trips, 0)) / totalTrips) : 0;
    if (top3Share >= 60 && top3.length === 3) {
      out.push({
        severity: 'warning',
        title: 'Three carriers move ' + top3Share + '% of everything',
        detail: top3.map(t => t.name).join(', ')
          + '. Concentration is efficient until one of them has a bad month, at which point '
          + "it is the whole network's bad month.",
        to: '/transporters/league', cta: 'See the split',
      });
    }

    const order: Record<Severity, number> = { critical: 0, warning: 1, info: 2 };
    return out.sort((a, b) => order[a.severity] - order[b.severity]).slice(0, 6);
  }, [matrix, cur, states, trans]);

  // daily volume + 7-day moving average
  const volume = useMemo(() => {
    const s = ts?.series ?? [];
    return s.map((r: any, i: number) => {
      const win = s.slice(Math.max(0, i - 6), i + 1);
      return { ...r, ma7: win.reduce((a: number, x: any) => a + x.trips, 0) / win.length };
    });
  }, [ts]);

  const split = cur.otd_pct != null ? [
    { name: 'On time', value: cur.otd_pct, color: '#22c55e' },
    { name: 'Delayed', value: Math.max(100 - cur.otd_pct, 0), color: tc('#ef4444') },
  ] : [];

  return (
    <PageContainer title="🏭 Executive Overview">
      {/* KPI cards */}
      {kpiLoading ? <Spinner /> : (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
          <KPICard label="Total Trips" value={formatNumber(cur.trips)} icon={Factory} color="blue"
            info={KPI_INFO.totalTrips} onDrill={drillTrips} drillLabel="List trips in filter" />
          <KPICard label="On-time Delivery" value={cur.otd_pct != null ? `${cur.otd_pct}%` : '—'} icon={GaugeIcon}
            color={cur.otd_pct != null && cur.otd_pct >= OTD_TARGET ? 'green' : 'red'} info={KPI_INFO.otd} />
          <KPICard label="Avg Transit" value={cur.avg_transit_hours != null ? `${cur.avg_transit_hours} h` : '—'} icon={Clock} color="amber" info={KPI_INFO.avgTransit} />
          <KPICard label="Total Distance" value={cur.total_km != null ? `${formatNumber(cur.total_km)} km` : '—'} icon={RouteIcon} color="cyan" info={KPI_INFO.totalKm} />
          <KPICard label="Active Transporters" value={formatNumber(cur.transporters)} icon={Users} color="purple" info={KPI_INFO.transporters} />
          <KPICard label="Unique Vehicles" value={formatNumber(cur.vehicles)} icon={Truck} color="blue" info={KPI_INFO.vehicles} />
          <KPICard label="Avg Detention" value={cur.avg_detention_hours != null ? `${cur.avg_detention_hours} h` : '—'} icon={Timer} color="amber" info={KPI_INFO.avgDetention} />
          <KPICard label="Speed Alerts / Trip" value={cur.avg_violations_per_trip ?? '—'} icon={AlertTriangle} color="red" info={KPI_INFO.violationsPerTrip} />
        </div>
      )}

      {/* secondary KPI strip */}
      {!kpiLoading && (
        <div className="grid grid-cols-2 md:grid-cols-6 gap-3 mb-6">
          {[
            ['Median transit', cur.median_transit_hours, 'h', KPI_INFO.medianTransit],
            ['Avg delay when late', cur.avg_delay_when_late_hours, 'h', KPI_INFO.delayWhenLate],
            ['Plant vivo', cur.avg_plant_vivo_hours, 'h', KPI_INFO.plantVivo],
            ['Dispatch lead', cur.avg_dispatch_lead_hours, 'h', KPI_INFO.dispatchLead],
            ['GPS uptime', cur.avg_gps_uptime, '%', KPI_INFO.gpsUptime],
            ['Market share', cur.market_share_pct, '%', KPI_INFO.marketShare],
          ].map(([label, v, unit, info]) => (
            <div key={label as string} className="bg-gray-900/70 border border-gray-800 rounded-lg px-3 py-2">
              <div className="flex items-center gap-1">
                <p className="text-xs text-gray-500">{label as string}</p>
                <InfoDot title={label as string} what={(info as any).what} formula={(info as any).formula} />
              </div>
              <p className="text-sm font-bold text-gray-200">{v != null ? `${v}${unit}` : '—'}</p>
            </div>
          ))}
        </div>
      )}

      {/* ------------------------- what needs attention ------------------- */}
      {!!findings.length && (
        <ChartCard title="What needs attention" icon={Siren} iconColor="text-red-400"
          className="mb-6"
          explain="Read off the same filtered window as every chart below, ranked by what it costs to ignore. Each one links to the page that shows the working - this band is the summary, not the evidence.">
          <div className="grid md:grid-cols-2 gap-3">
            {findings.map((fnd, i) => (
              <div key={i} className={`border rounded-lg p-3 ${SEVERITY_STYLE[fnd.severity]}`}>
                <div className="flex items-start gap-2">
                  <span className={`w-2 h-2 rounded-full mt-1.5 shrink-0 ${SEVERITY_DOT[fnd.severity]}`} />
                  <div className="min-w-0">
                    <p className="text-sm font-semibold text-gray-100 leading-snug">{fnd.title}</p>
                    <p className="text-xs text-gray-400 mt-1 leading-relaxed">{fnd.detail}</p>
                    <Link to={fnd.to}
                      className="inline-flex items-center gap-1 text-xs text-blue-400 hover:text-blue-300 mt-1.5">
                      {fnd.cta} <ArrowRight className="w-3 h-3" />
                    </Link>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </ChartCard>
      )}

      <div className="grid lg:grid-cols-3 gap-6 mb-6">
        <ChartCard title="Daily Dispatch Volume"
        method={{
          formula: "Trips whose departure timestamp falls on each date, with a trailing 7-day mean overlaid.",
          plot: "dualAxis",
        }} icon={TrendingUp} iconColor="text-cyan-400" className="lg:col-span-2"
          explain="Trips leaving the plant each day, with a 7-day moving average — dips reveal production, ordering or truck-availability problems.">
          <DualAxisChart data={volume} xKey="period" height={280} series={[
            { key: 'trips', label: 'Trips', color: tc('#06b6d4'), type: 'area' },
            { key: 'ma7', label: '7-day avg', color: '#ffd166', dashed: true },
          ]} />
        </ChartCard>
        <ChartCard title="OTD vs Target"
        method={{
          formula: "On-time trips ÷ trips with a recorded delivery status, against the 95% service target.",
          plot: "gauge",
          caveat: "Trips with no delivery status are excluded from both sides, so this is a rate over judged trips, not over all trips.",
        }} icon={GaugeIcon} iconColor="text-emerald-400"
          explain={`Share of delivered trips on time vs the ${OTD_TARGET}% service target — every point below target is a broken customer promise.`}>
          <GaugeChart value={cur.otd_pct} target={OTD_TARGET} label="OTD %" height={250} />
        </ChartCard>
      </div>

      <div className="grid lg:grid-cols-3 gap-6 mb-6">
        <ChartCard title="Transit Time vs On-time Performance"
        method={{
          formula: "Average transit hours per period on the left axis; on-time rate on the right.",
          plot: "dualAxis",
          caveat: "Orange rising with green falling means journeys genuinely slowed. Green falling while orange is flat means the promises were set too tight, which is a planning fix, not a carrier one.",
        }} icon={Clock} iconColor="text-amber-400" className="lg:col-span-2"
          explain="Orange rising while green falls = journeys genuinely slower; OTD falling with flat transit = promises are set too tight.">
          <DualAxisChart data={ts?.series ?? []} xKey="period" height={280} rightDomain={[0, 100]}
            series={[
              { key: 'avg_transit_hours', label: 'Avg transit (h)', color: tc('#f59e0b') },
              { key: 'otd_pct', label: 'OTD %', color: '#22c55e', axis: 'right' },
            ]} />
        </ChartCard>
        <ChartCard title="Delivery Status Split"
        method={{
          formula: "Share of judged trips recorded on time versus delayed.",
          plot: "donut",
        }} icon={PieIcon} iconColor="text-green-400"
          explain="Share of trips delivered on time vs delayed (of trips with a recorded delivery status).">
          {split.length ? <DonutChart data={split} height={280} /> :
            <p className="text-gray-500 text-sm py-8">No delivery status recorded yet</p>}
        </ChartCard>
      </div>

      <div className="grid lg:grid-cols-2 gap-6 mb-6">
        <ChartCard title="Top 10 Destinations"
        method={{
          formula: "Trip count per destination node, ranked; bar colour is that destination's on-time rate.",
          plot: "hbar",
        }} icon={MapPin} iconColor="text-blue-400"
          explain="Busiest destinations coloured by OTD — a long red bar is a major customer location getting poor service; fix those lanes first.">
          <HBarChart data={(dest?.rows ?? []).slice(0, 10)} nameKey="name" valueKey="trips"
            valueLabel="Trips" colorByKey="otd_pct" colorLo={50} colorHi={100} />
        </ChartCard>
        <ChartCard title="Volume Share — Top 10 Transporters"
        method={{
          formula: "Each carrier's trips as a share of the filtered total.",
          plot: "donut",
          caveat: "Two or three dominant slices is concentration risk: efficient until one of them has a bad month.",
        }} icon={Users} iconColor="text-purple-400"
          explain="Concentration risk: two or three dominant slices mean the business depends on few carriers.">
          <DonutChart data={(trans?.rows ?? []).slice(0, 10).map((r: any) => ({ name: r.name, value: r.trips }))}
            height={300} innerRadius={55} />
        </ChartCard>
      </div>

      {/* -------------------------- deep-dive tables ---------------------- */}
      <h2 className="text-sm font-semibold text-gray-300 mb-3">
        The tables behind the headline
      </h2>
      <p className="text-xs text-gray-500 mb-4 max-w-3xl">
        The first rows of each deep-dive page, ranked worst-first and limited to
        rows with at least {MIN_TRIPS} trips - below that a rate is noise, and putting
        it at the top of a table is how a two-trip carrier ends up in a review meeting.
      </p>

      <div className="grid lg:grid-cols-2 gap-6 mb-6">
        <DeepDive
          title="Carriers to look at first" 
          method={{
            formula: "Carriers with at least MIN_TRIPS trips in the filter, ranked by on-time rate ascending. Share is that carrier's trips as a percentage of all filtered trips.",
            plot: "table",
            caveat: "Some of these hold the hardest lanes. Check lane difficulty on the carrier's profile before acting.",
          }}
          icon={Truck} iconColor="text-red-400"
          explain="Lowest on-time rate among carriers running real volume. Check lane difficulty before acting - some of these hold the hardest routes."
          to="/transporters/league" linkLabel="All transporters"
          rows={worstCarriers}
          empty={`No carrier has ${MIN_TRIPS} trips in this window.`}
          columns={[
            { key: 'name', label: 'Transporter',
              render: (r: GroupRow) => (
                <Link to={`/transporters/${encodeURIComponent(r.name)}`}
                  className="text-blue-400 hover:text-blue-300">{r.name}</Link>) },
            { key: 'trips', label: 'Trips' },
            { key: 'otd_pct', label: 'OTD %', render: (r: GroupRow) => otdCell(r.otd_pct) },
            { key: 'avg_transit_hours', label: 'Transit',
              render: (r: GroupRow) => r.avg_transit_hours != null ? `${r.avg_transit_hours} h` : '\u2014' },
            { key: 'share_pct', label: 'Share',
              render: (r: GroupRow) => `${r.share_pct ?? 0}%` },
          ]} />

        <DeepDive
          title="Lanes running behind their own ETA" 
          method={{
            formula: "Mean actual transit minus mean QUOTED transit per destination, most positive first, over lanes with enough trips to judge.",
            plot: "table",
            caveat: "This measures a broken promise, not a long road — a 40-hour lane quoted at 40 hours scores zero here.",
          }}
          icon={RouteIcon} iconColor="text-amber-400"
          explain='"Vs plan" is measured against the ETA quoted for that lane, so a positive number is a broken promise rather than simply a long road.'
          to="/analytics/lanes" linkLabel="All lanes"
          rows={slippingLanes}
          empty="No lane has enough trips to judge against plan."
          columns={[
            { key: 'name', label: 'Destination' },
            { key: 'trips', label: 'Trips' },
            { key: 'schedule_variance_hours', label: 'Vs plan',
              render: (r: GroupRow) => r.schedule_variance_hours != null ? (
                <span className={r.schedule_variance_hours > 0 ? 'text-red-400 font-semibold' : 'text-emerald-400'}>
                  {r.schedule_variance_hours > 0 ? '+' : ''}{r.schedule_variance_hours} h
                </span>) : '\u2014' },
            { key: 'otd_pct', label: 'OTD %', render: (r: GroupRow) => otdCell(r.otd_pct) },
            { key: 'avg_transit_hours', label: 'Transit',
              render: (r: GroupRow) => r.avg_transit_hours != null ? `${r.avg_transit_hours} h` : '\u2014' },
          ]} />

        <DeepDive
          title="Where trucks wait longest" 
          method={{
            formula: "Mean detention hours per destination, descending.",
            plot: "table",
            caveat: "Waits at your own plant are a bay-scheduling problem; waits at a customer are a conversation with that customer.",
          }}
          icon={Timer} iconColor="text-orange-400"
          explain="Average detention by destination. Long waits at your own plant are a bay-scheduling problem; long waits at a customer are a conversation with that customer."
          to="/analytics/distributions" linkLabel="Distributions"
          rows={detentionLanes}
          empty="No detention recorded."
          columns={[
            { key: 'name', label: 'Destination' },
            { key: 'trips', label: 'Trips' },
            { key: 'avg_detention_hours', label: 'Detention',
              render: (r: GroupRow) => (
                <span className={(r.avg_detention_hours ?? 0) > 8 ? 'text-red-400 font-semibold' : 'text-amber-400'}>
                  {hrs(r.avg_detention_hours)}
                </span>) },
            { key: 'otd_pct', label: 'OTD %', render: (r: GroupRow) => otdCell(r.otd_pct) },
          ]} />

        <DeepDive
          title="Biggest regions" 
          method={{
            formula: "Trips per destination state, descending, with that state's on-time rate.",
            plot: "table",
            caveat: "States with too few judged trips show 'too few' rather than a rate.",
          }}
          icon={MapIcon} iconColor="text-cyan-400"
          explain="Freight by destination state, largest first, with the on-time rate it lands at. A big region below target is a network-level problem, not a lane-level one."
          to="/analytics/geo" linkLabel="Region map"
          rows={topStates}
          empty="No destination resolved to a state."
          columns={[
            { key: 'state', label: 'State' },
            { key: 'trips', label: 'Trips' },
            { key: 'share_pct', label: 'Share',
              render: (r: StateRow) => `${r.share_pct ?? 0}%` },
            { key: 'otd_pct', label: 'OTD %',
              render: (r: StateRow) => r.judged_trips < MIN_TRIPS
                ? <span className="text-gray-500">too few</span> : otdCell(r.otd_pct) },
            { key: 'avg_transit_hours', label: 'Transit',
              render: (r: StateRow) => r.avg_transit_hours != null ? `${r.avg_transit_hours} h` : '\u2014' },
          ]} />
      </div>

      <ChartCard title="Trip Lifecycle Funnel"
        method={{
          formula: "How many trips recorded each milestone: booked, departed, arrived, unloaded, closed, delivery status.",
          plot: "funnel",
          caveat: "A step-down is trips that never RECORDED that milestone — a tracking gap, not vanished trucks.",
        }} icon={Filter} iconColor="text-cyan-400"
        explain="How many trips record each milestone — step-downs reveal tracking gaps (trips that never record an arrival), not vanished trucks. Blind spots undermine every other metric.">
        {fun ? <FunnelStages stages={fun.stages} /> : <Spinner />}
      </ChartCard>
    </PageContainer>
  );
}
