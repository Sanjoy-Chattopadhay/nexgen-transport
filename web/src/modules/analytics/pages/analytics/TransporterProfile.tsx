import { useMemo, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import {
  ArrowLeft, Truck, Users, Route as RouteIcon, MapPin, Satellite, ShieldAlert,
  TrendingUp, Clock, Box, Activity, AlertTriangle, IdCard, Gauge, Radar,
  CalendarClock, PieChart as PieIcon,
} from 'lucide-react';
import PageContainer from '../../components/layout/PageContainer';
import Spinner from '../../components/ui/Spinner';
import ChartCard from '../../components/ui/ChartCard';
import DataTable from '../../components/ui/DataTable';
import Badge from '../../components/ui/Badge';
import DualAxisChart from '../../components/charts/DualAxisChart';
import BarChart from '../../components/charts/BarChart';
import DonutChart from '../../components/charts/DonutChart';
import HistogramChart from '../../components/charts/HistogramChart';
import MatrixHeatmap from '../../components/charts/MatrixHeatmap';
import HBarChart from '../../components/charts/HBarChart';
import BoxPlotChart from '../../components/charts/BoxPlotChart';
import { useApi } from '../../hooks/useApi';
import { useTTAFilters } from '../../components/tta/dashboard/FilterContext';
import { getTransporter } from '../../services/transporters';
import type {
  TransporterDetail, TransporterGpsQuality, VehicleRow, DriverRow, StateRow,
  CarrierGrade, FleetShape, OriginDetention,
} from '../../services/transporters';
import { getRunningPattern, getCarrierSafety, getSpeedEvents } from '../../services/speed';
import type { CarrierSafetyRow, SpeedEvent } from '../../services/speed';
import { formatNumber, formatDateTime } from '../../lib/formatters';
import { downloadCsv } from '../../lib/csv';
import { tc } from '../../../../core/theme';

const TABS = [
  { id: 'overview', label: 'Overview', icon: IdCard },
  { id: 'performance', label: 'Performance', icon: TrendingUp },
  { id: 'network', label: 'Lanes & regions', icon: RouteIcon },
  { id: 'fleet', label: 'Fleet & drivers', icon: Users },
  { id: 'tracking', label: 'Tracking & safety', icon: Satellite },
  { id: 'trips', label: 'Trips', icon: Truck },
] as const;
type TabId = typeof TABS[number]['id'];

const GRADE_TONE: Record<string, string> = {
  A: 'bg-emerald-500/15 text-emerald-300 border-emerald-700',
  B: 'bg-lime-500/15 text-lime-300 border-lime-700',
  C: 'bg-amber-500/15 text-amber-300 border-amber-700',
  D: 'bg-orange-500/15 text-orange-300 border-orange-700',
  E: 'bg-red-500/15 text-red-300 border-red-700',
};

/**
 * Two colours, not three.
 *
 * The insight list used to render a third "info" tone in grey, which read as a
 * finding you could skip. Everything here is either something this carrier does
 * WELL or something to raise with it, so neutral was the wrong signal — a line
 * worth showing is worth taking a side on. Anything genuinely neutral is now
 * context and lives outside this list.
 */
const TONE: Record<string, string> = {
  good: 'border-emerald-800 bg-emerald-950/30 text-emerald-100',
  bad: 'border-red-900 bg-red-950/30 text-red-100',
};

const GRADE_COLOUR: Record<string, string> = {
  A: 'text-emerald-400', B: 'text-emerald-400',
  C: 'text-red-400', D: 'text-red-400',
};

/** The grade, and the marking behind it. */
function GradePanel({ grade }: { grade: CarrierGrade }) {
  if (!grade?.components?.length) return null;
  const tone = GRADE_COLOUR[grade.grade] ?? 'text-gray-400';
  return (
    <>
      <div className="flex items-start gap-5 flex-wrap mb-4">
        <div className="text-center">
          <p className={`text-5xl font-bold leading-none ${tone}`}>{grade.grade}</p>
          <p className="text-xs text-gray-500 mt-1">
            {grade.rated ? `${grade.grade_score} / 100` : 'not rated'}
          </p>
        </div>
        <p className="text-xs text-gray-400 leading-relaxed max-w-xl">
          {grade.rated ? grade.meaning : grade.reason}
          {grade.rated && grade.measured_weight < 100 && (
            <span className="text-amber-300/80">
              {' '}Only {grade.measured_weight}% of the grade could be measured for this
              carrier; the rest was dropped rather than scored as zero.
            </span>
          )}
        </p>
      </div>
      <div className="space-y-1.5">
        {grade.components.map(c => {
          const pass = (c.points ?? 0) >= 60;
          return (
            <div key={c.key} className="bg-gray-950/60 border border-gray-800 rounded-lg px-3 py-2">
              <div className="flex items-center justify-between gap-3 flex-wrap">
                <p className="text-xs text-gray-200">
                  {c.label}
                  <span className="text-gray-500"> · weight {c.weight}</span>
                </p>
                <p className="text-xs shrink-0">
                  <span className="text-gray-400">
                    {c.value != null ? `${c.value}${c.unit}` : 'not measured'}
                  </span>
                  <span className={`ml-2 font-semibold ${
                    c.points == null ? 'text-gray-600' : pass ? 'text-emerald-400' : 'text-red-400'}`}>
                    {c.points == null ? '—' : `${c.points}/100`}
                  </span>
                </p>
              </div>
              <p className="text-xs text-gray-500 mt-1 leading-snug">{c.why}</p>
              <p className="text-xs text-gray-600 mt-1">
                Bands: {c.bands.map(b => `${c.lower_is_better ? '≤' : '≥'}${b.at}${c.unit} → ${b.points}`).join(' · ')}
              </p>
            </div>
          );
        })}
      </div>
      <p className="text-xs text-gray-500 mt-3">
        Grade cut-offs: {grade.bands.map(b => `${b.grade} ≥ ${b.at}`).join(' · ')}.
        These are ABSOLUTE, so a grade does not move when another carrier's numbers
        change — unlike the ranking score, which is a percentile against the current filter.
      </p>
    </>
  );
}

/** The fleet as it actually is, replacing a mean that described nobody. */
function FleetShapePanel({ shape }: { shape: FleetShape }) {
  if (!shape?.vehicles) return <p className="text-gray-500 text-sm py-4">No vehicles recorded.</p>;
  // Three bands, not two. A 48/52 split is not a "dedicated fleet", and calling
  // it one at the boundary is exactly the kind of confident wrong answer these
  // panels exist to stop.
  const once = shape.single_trip_pct ?? 0;
  const mode = once >= 65 ? 'brokered' : once >= 35 ? 'mixed' : 'dedicated';
  return (
    <>
      <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
        <Stat label="Vehicles used" value={formatNumber(shape.vehicles)} />
        <Stat label="Typical vehicle" value={`${shape.median_trips_per_vehicle} trips`}
          sub="median, not mean" />
        <Stat label="Busiest vehicle" value={`${shape.max_trips_per_vehicle} trips`} />
        <Stat label="Used once only" value={`${shape.single_trip_pct}%`}
          sub={`${shape.single_trip_vehicles} of ${shape.vehicles} trucks`}
          tone={mode === 'brokered' ? 'text-red-400' : mode === 'mixed' ? 'text-amber-400' : 'text-emerald-400'} />
        <Stat label="Top 10 trucks carry" value={`${shape.top10_share_pct}%`} sub="of its trips" />
      </div>
      <p className="text-xs text-gray-500 mt-3 leading-relaxed">
        Read the median and the single-use share, not an average.{' '}
        {mode === 'brokered' && `${once}% of this carrier's trucks appear exactly once, so it is
          brokering capacity rather than running a dedicated fleet — vehicle-level quality
          standards will not stick to trucks it does not control.`}
        {mode === 'mixed' && `${once}% of its trucks appear once and the rest come back, so it runs
          a core fleet topped up with hired capacity. Vehicle standards are enforceable on the
          repeat trucks only, and that is the half worth writing into a contract.`}
        {mode === 'dedicated' && `Only ${once}% of its trucks appear once, so this is a dedicated
          fleet and vehicle-level standards are enforceable across it.`}
      </p>
    </>
  );
}

/** Three origin windows, and how much the declared one misses. */
function OriginDetentionPanel({ d }: { d: OriginDetention }) {
  if (!d?.declared) return null;
  const measured = d.measured_trips > 0;
  return (
    <>
      <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
        {[
          ['Declared (TMS)', d.declared, 'Plant entry → gate-out stamp. What you are billed against.'],
          ['Still inside the fence', d.fence_tail, 'After the gate-out stamp, before clearing the 10 km plant fence.'],
          ['True hold at origin', d.true_total, 'The two together, where the GPS trail confirmed the exit.'],
        ].map(([label, w, note]) => {
          const win = w as typeof d.declared;
          return (
            <div key={label as string} className="bg-gray-950/60 border border-gray-800 rounded-lg p-3">
              <p className="text-xs text-gray-500">{label as string}</p>
              <p className="text-2xl font-bold text-gray-100">
                {win.median_hours != null ? `${win.median_hours} h` : '—'}
                <span className="text-xs text-gray-500 font-normal"> median</span>
              </p>
              <p className="text-xs text-gray-500 mt-1">
                {win.trips} trips · 90th pct {win.p90_hours ?? '—'} h
                {win.fleet_median_hours != null && ` · fleet ${win.fleet_median_hours} h`}
              </p>
              <p className="text-xs text-gray-600 mt-1 leading-snug">{note as string}</p>
            </div>
          );
        })}
      </div>
      <p className="text-xs mt-3 leading-relaxed">
        {measured ? (
          <span className={(d.declared_understates_by_pct ?? 0) > 25 ? 'text-red-300' : 'text-emerald-300'}>
            The declared figure misses {d.declared_understates_by_pct}% of the true hold —
            measured on the {d.measured_trips} of {d.total_trips} trips whose fence exit the GPS
            trail confirmed.
          </span>
        ) : (
          <span className="text-gray-500">
            No trip for this carrier had a confirmed geofence exit, so only the declared window
            can be shown. That is a tracking gap, not a short hold.
          </span>
        )}
      </p>
    </>
  );
}

const pct = (v: number | null | undefined) => (v == null ? '—' : `${v}%`);
const hh = (v: number | null | undefined) => (v == null ? '—' : `${v.toFixed(1)} h`);

/** Small labelled figure used across the bio strip and the stat rows. */
function Stat({ label, value, sub, tone }: {
  label: string; value: React.ReactNode; sub?: string; tone?: string;
}) {
  return (
    <div className="bg-gray-950/60 border border-gray-800 rounded-lg px-3 py-2">
      <p className="text-xs text-gray-500 truncate">{label}</p>
      <p className={`text-sm font-bold ${tone ?? 'text-gray-200'}`}>{value}</p>
      {sub && <p className="text-xs text-gray-600 truncate">{sub}</p>}
    </div>
  );
}

/** A KPI shown next to the median carrier, because the number alone is not a
 *  verdict — 18 h transit is good on trunk lanes and terrible on local runs. */
function VsFleet({ label, value, fleet, unit = '', lowerBetter = false }: {
  label: string; value: number | null | undefined;
  fleet: number | null | undefined; unit?: string; lowerBetter?: boolean;
}) {
  const gap = value != null && fleet != null ? value - fleet : null;
  const better = gap == null ? null : lowerBetter ? gap < 0 : gap > 0;
  return (
    <div className="bg-gray-900 border border-gray-800 rounded-lg px-4 py-3">
      <p className="text-xs text-gray-500 mb-1">{label}</p>
      <p className="text-xl font-bold text-gray-100">
        {value != null ? `${value}${unit}` : '—'}
      </p>
      <p className="text-xs mt-0.5">
        {gap == null ? <span className="text-gray-600">no fleet median</span> : (
          <span className={better ? 'text-emerald-400' : 'text-red-400'}>
            {gap > 0 ? '+' : ''}{Math.round(gap * 10) / 10}{unit} vs median {fleet}{unit}
          </span>
        )}
      </p>
    </div>
  );
}

function GpsQualityPanel({ q }: { q: TransporterGpsQuality }) {
  if (!q?.available) {
    return (
      <p className="text-gray-500 text-sm py-6">
        No GPS coverage score for this carrier{q?.reason ? ` — ${q.reason}` : ''}.
      </p>
    );
  }
  const ok = q.gps_ok_pct ?? 0;
  const width = q.gps_ok_ci_width;
  return (
    <>
      <div className="flex items-end gap-6 mb-4 flex-wrap">
        <div>
          <p className="text-xs text-gray-500">Trips tracked cleanly</p>
          <p className={`text-3xl font-bold ${ok >= 90 ? 'text-emerald-400' : ok >= 70 ? 'text-amber-400' : 'text-red-400'}`}>
            {ok}%
          </p>
          <p className="text-xs text-gray-500">
            {q.gps_ok_ci_low}–{q.gps_ok_ci_high}% at 95% confidence
            {width != null && width > 20 && ' — too few trips to rank confidently'}
          </p>
        </div>
        <div>
          <p className="text-xs text-gray-500">Rank</p>
          <p className="text-2xl font-bold text-gray-200">
            {q.rank ?? '—'}<span className="text-sm text-gray-500"> / {q.carriers_ranked}</span>
          </p>
          <p className="text-xs text-gray-500">fleet median {pct(q.fleet_median_ok_pct)}</p>
        </div>
      </div>
      <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
        <Stat label="Trips scored" value={formatNumber(q.trips)} />
        <Stat label="Never pinged" value={q.silent_trips ?? 0} sub={pct(q.silent_pct)}
          tone={q.silent_trips ? 'text-red-400' : 'text-gray-200'} />
        <Stat label="Died at origin" value={q.died_at_origin ?? 0} sub={pct(q.died_at_origin_pct)}
          tone={q.died_at_origin ? 'text-amber-400' : 'text-gray-200'} />
        <Stat label="Gappy trails" value={q.gappy ?? 0} sub="uptime under 80%" />
        <Stat label="Median pings/trip" value={formatNumber(q.median_ping_count)} />
      </div>
      <p className="text-xs text-gray-500 mt-3">
        A trip counts as cleanly tracked when it pinged, started reporting within
        30 minutes of departure, kept above 80% uptime and did not go dark at the
        plant. The confidence band is why the rank is trustworthy: a carrier with
        five trips at 100% is not measurably better than one with two hundred at 92%.
      </p>
    </>
  );
}

export default function TransporterProfile() {
  const { name = '' } = useParams();
  const carrier = decodeURIComponent(name);
  const { params, paramsKey } = useTTAFilters();
  const [tab, setTab] = useState<TabId>('overview');

  const { data, loading, error } = useApi<TransporterDetail>(
    () => getTransporter(carrier, params as never), [paramsKey, carrier]);
  const { data: pattern } = useApi(
    () => getRunningPattern(params, carrier), [paramsKey, carrier]);
  const { data: safety } = useApi(
    () => getCarrierSafety(60, 20, 1, params), [paramsKey]);
  const { data: violations } = useApi(
    () => getSpeedEvents(60, 20, params, { transporter: carrier, rows: 100 }),
    [paramsKey, carrier]);

  const mySafety: CarrierSafetyRow | undefined = useMemo(
    () => safety?.rows.find(r => r.transporter === carrier), [safety, carrier]);

  const bio = data?.bio;
  const k = data?.kpis;
  const fleet = data?.fleet;

  const patternBars = useMemo(
    () => (pattern?.hours ?? []).map(h => ({
      label: h.label,
      moving: h.moving_hours ?? 0,
      stopped: h.stopped_hours ?? 0,
    })), [pattern]);

  if (loading) return <PageContainer><Spinner /></PageContainer>;
  if (error || !data) {
    return (
      <PageContainer>
        <Link to="/transporters/league" className="text-blue-400 text-sm flex items-center gap-1.5 mb-4">
          <ArrowLeft className="w-4 h-4" /> Back to transporters
        </Link>
        <p className="text-red-400 text-sm">{error ?? 'Carrier not found in this filter window.'}</p>
      </PageContainer>
    );
  }

  return (
    <PageContainer>
      {/* ------------------------------- header ------------------------------ */}
      <Link to="/transporters/league"
        className="text-blue-400 hover:text-blue-300 text-sm flex items-center gap-1.5 mb-4">
        <ArrowLeft className="w-4 h-4" /> All transporters
      </Link>

      <div className="bg-gray-900 rounded-xl border border-gray-800 p-5 mb-6">
        <div className="flex items-start justify-between gap-4 flex-wrap mb-4">
          <div className="min-w-0">
            <h1 className="text-2xl font-bold text-white flex items-center gap-3">
              <Truck className="w-6 h-6 text-blue-400 shrink-0" />
              <span className="truncate">{carrier}</span>
            </h1>
            <p className="text-xs text-gray-500 mt-1">
              {bio?.first_trip} → {bio?.last_trip} · active on {bio?.active_days} of{' '}
              {bio?.window_days} days ({pct(bio?.activity_pct)} of the relationship)
            </p>
          </div>
          <div className="flex items-center gap-3">
            {k?.grade && k.grade !== '—' && (
              <div className={`px-4 py-2 rounded-xl border text-center ${GRADE_TONE[k.grade] ?? TONE.info}`}>
                <p className="text-2xl font-bold leading-none">{k.grade}</p>
                <p className="text-xs opacity-80 mt-0.5">grade</p>
              </div>
            )}
            <div className="px-4 py-2 rounded-xl border border-gray-800 bg-gray-950 text-center">
              <p className="text-2xl font-bold text-gray-100 leading-none">
                {k?.score ?? '—'}
              </p>
              <p className="text-xs text-gray-500 mt-0.5">
                score{k?.rank ? ` · rank ${k.rank}` : ''}
              </p>
            </div>
          </div>
        </div>

        {/* ------------------------------ bio strip -------------------------- */}
        <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-8 gap-3">
          <Stat label="Trips" value={formatNumber(bio?.trips)} sub={`${pct(bio?.share_pct)} of freight`} />
          <Stat label="Vehicles" value={formatNumber(bio?.vehicles)}
            sub={bio?.top_make ? `mostly ${bio.top_make}` : undefined} />
          <Stat label="Drivers" value={formatNumber(bio?.drivers)} />
          <Stat label="Lanes" value={formatNumber(bio?.lanes)} sub={`${bio?.destinations} destinations`} />
          <Stat label="States served" value={formatNumber(bio?.states)} />
          <Stat label="Customers" value={formatNumber(bio?.consignees)}
            sub={`${bio?.consignors} consignors`} />
          <Stat label="Distance" value={`${formatNumber(bio?.total_km)} km`} />
          <Stat label="Market fleet" value={pct(bio?.market_pct)}
            sub={bio?.own_pct ? `${bio.own_pct}% own` : 'no owned trucks'}
            tone={(bio?.market_pct ?? 0) > 70 ? 'text-amber-400' : 'text-gray-200'} />
        </div>
      </div>

      {/* -------------------------------- tabs ------------------------------- */}
      <div className="flex gap-1 bg-gray-900 border border-gray-800 rounded-lg p-1 mb-6 w-fit flex-wrap">
        {TABS.map(t => (
          <button key={t.id} onClick={() => setTab(t.id)}
            className={`flex items-center gap-2 px-3 py-1.5 rounded-md text-sm font-medium transition-colors ${
              tab === t.id ? 'bg-blue-600/15 text-blue-400' : 'text-gray-400 hover:text-gray-200'}`}>
            <t.icon className="w-4 h-4" /> {t.label}
          </button>
        ))}
      </div>

      {/* ------------------------------ overview ----------------------------- */}
      {tab === 'overview' && (
        <>
          <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-5 gap-4 mb-6">
            <VsFleet label="On-time delivery" value={k?.otd_pct} fleet={fleet?.median_otd_pct} unit="%" />
            <VsFleet label="Average transit" value={k?.avg_transit_hours}
              fleet={fleet?.median_transit_hours} unit=" h" lowerBetter />
            <VsFleet label="Detention at plant" value={k?.avg_detention_hours}
              fleet={fleet?.median_detention_hours} unit=" h" lowerBetter />
            <VsFleet label="Vs its own promised ETA" value={k?.schedule_variance_hours}
              fleet={fleet?.median_schedule_variance_hours} unit=" h" lowerBetter />
            <VsFleet label="GPS uptime" value={k?.avg_gps_uptime}
              fleet={fleet?.median_gps_uptime} unit="%" />
          </div>

          <ChartCard title="Grade" icon={Gauge} iconColor="text-emerald-400" className="mb-6"
            explain="Marked against fixed published bands, so the grade means the same thing this month as last and does not move when another carrier's numbers change."
            method={{
              formula: "Five components, each scored 0-100 against absolute published bands and combined by weight: on-time 40, speed 20, origin detention 15, tracking quality 15, ETA adherence 10. Components with no data are dropped and the weights renormalised, so a carrier is never scored zero for something nobody could measure.",
              plot: "table",
              caveat: "Needs at least 10 trips to be graded at all. Below that the letter would be noise dressed as a verdict, so the carrier is shown as not rated.",
            }}>
            <GradePanel grade={data.grade} />
          </ChartCard>

          <ChartCard title="What its fleet actually looks like" icon={Truck} iconColor="text-cyan-400"
            className="mb-6"
            explain="The shape of the fleet, not its average — because an average vehicle here usually describes no vehicle at all."
            method={{
              formula: "Trips grouped by vehicle. Median trips per vehicle, share of vehicles appearing exactly once, and the share of all trips carried by the ten busiest.",
              plot: "table",
              caveat: "This panel replaced a mean. '118 vehicles doing 2.8 trips each' was arithmetically right and described a fleet that does not exist: on this corpus the biggest carrier's median truck runs one trip while a handful run a dozen, so the mean sat between two populations and represented neither.",
            }}>
            <FleetShapePanel shape={data.fleet_shape} />
          </ChartCard>

          <ChartCard title="Detention at origin" icon={Clock} iconColor="text-amber-400" className="mb-6"
            explain="How long this carrier's trucks are actually held at the plant, and how much of it the declared figure misses."
            method={{
              formula: "Declared = plant entry to the gate-out stamp (the TMS figure). Fence tail = time still inside the 10 km plant geofence after that stamp. True hold = the two together, available only where the GPS trail confirmed the fence exit. Medians, because a single stuck truck moves a mean.",
              plot: "table",
              caveat: "The gate-out stamp fires before the truck has physically cleared the works, so the declared figure is structurally optimistic. Part of the hold is your own loading bay, not the carrier's fault.",
            }}>
            <OriginDetentionPanel d={data.origin_detention} />
          </ChartCard>

          <ChartCard title="What the numbers say"
        method={{
          formula: "Each line compares one of this carrier's KPIs against the MEDIAN qualifying carrier in the same filtered window, and states the gap in the metric's own units. Tone is set by whether the gap favours the carrier, with a 5% dead band around the median treated as ‘in line’.",
          plot: "table",
          caveat: "The median moves with the filter. Narrow the date window and a carrier can change verdict without changing behaviour.",
        }} icon={Radar} iconColor="text-purple-400"
            className="mb-6"
            explain="Each line compares this carrier with the median carrier in the same filtered window, so route difficulty and season are held roughly constant.">
            <div className="grid md:grid-cols-2 gap-2">
              {data.insights.filter(i => TONE[i.tone]).map((i, n) => (
                <div key={n} className={`border rounded-lg px-3 py-2 text-xs leading-relaxed ${TONE[i.tone]}`}>
                  {i.text}
                </div>
              ))}
              {!data.insights.length && (
                <p className="text-gray-500 text-sm">Not enough trips to assess this carrier.</p>
              )}
            </div>
          </ChartCard>

          <div className="grid lg:grid-cols-3 gap-6">
            <ChartCard title="Own vs market"
        method={{
          formula: "Share of this carrier's trips run on owned versus hired trucks.",
          plot: "donut",
          caveat: "A high market share means the carrier is brokering rather than running its own fleet — relevant before holding it to a fleet-quality standard.",
        }} icon={PieIcon} iconColor="text-amber-400"
              explain="Market (hired) trucks mean less control over vehicle condition and driver quality.">
              <DonutChart height={230}
                data={data.own_market.map(m => ({ name: m.name, value: m.trips }))} />
            </ChartCard>
            <ChartCard title="Vehicle mix"
        method={{
          formula: "This carrier's trips by derived vehicle class.",
          plot: "donut",
        }} icon={Truck} iconColor="text-cyan-400"
              explain="What this carrier actually puts on the road for you.">
              <DonutChart height={230}
                data={data.vehicle_category.map(m => ({ name: m.name, value: m.trips }))} />
            </ChartCard>
            <ChartCard title="Delivery outcomes"
        method={{
          formula: "Recorded delivery status across this carrier's trips.",
          plot: "donut",
          caveat: "Trips with no status are excluded here and from the on-time rate.",
        }} icon={Gauge} iconColor="text-emerald-400"
              explain="Recorded delivery status. Trips with no status are not counted in OTD either.">
              <DonutChart height={230}
                data={data.delivery.map(m => ({ name: m.name, value: m.trips }))} />
            </ChartCard>
          </div>
        </>
      )}

      {/* ----------------------------- performance --------------------------- */}
      {tab === 'performance' && (
        <>
          <ChartCard title="Month by month"
        method={{
          formula: "Trips per calendar month on the left axis, on-time rate on the right.",
          plot: "dualAxis",
          caveat: "Volume climbing while on-time falls is the pattern to catch early: the carrier has taken on more than its fleet can serve.",
        }} icon={TrendingUp} iconColor="text-cyan-400"
            className="mb-6"
            explain="Volume against on-time rate. Volume climbing while OTD falls is the pattern to catch early — it usually means the carrier has taken on more than its fleet can serve.">
            <DualAxisChart data={data.monthly} xKey="month" height={300} rightDomain={[0, 100]}
              series={[
                { key: 'trips', label: 'Trips', color: tc('#06b6d4'), type: 'bar' },
                { key: 'otd_pct', label: 'OTD %', color: '#22c55e', axis: 'right' },
              ]} />
          </ChartCard>

          <div className="grid lg:grid-cols-2 gap-6 mb-6">
            <ChartCard title="Transit time spread"
        method={{
          formula: "Distribution of this carrier's trip transit hours. Variability is the coefficient of variation — standard deviation ÷ mean, as a percentage — so it is comparable between a 4-hour lane and a 40-hour one.",
          plot: "histogram",
          caveat: "The 90th percentile is the number to plan against: one trip in ten takes at least that long.",
        }} icon={Box} iconColor="text-blue-400"
              explain="A narrow peak is a carrier you can plan around. A long tail means the average is a fiction and downstream stock has to absorb the difference.">
              {/* Box first: the user's point is that a histogram answers "what
                  shape" while the question is "how spread out", and quartiles
                  answer that directly. The histogram stays underneath for the
                  shape of the tail. */}
              <BoxPlotChart unit="h" groups={[{
                group: 'Transit hours',
                count: data.transit_spread.stats?.count ?? 0,
                min: data.transit_spread.stats?.min ?? 0,
                max: data.transit_spread.stats?.max ?? 0,
                q1: data.transit_spread.stats?.q1 ?? 0,
                median: data.transit_spread.stats?.median ?? 0,
                q3: data.transit_spread.stats?.q3 ?? 0,
                whisker_lo: data.transit_spread.stats?.whisker_lo ?? 0,
                whisker_hi: data.transit_spread.stats?.whisker_hi ?? 0,
                mean: data.transit_spread.stats?.mean ?? 0,
                outliers: [],
              }]} />
              <HistogramChart bins={data.transit_spread.histogram}
                mean={data.transit_spread.stats?.mean} median={data.transit_spread.stats?.median} />
              {data.transit_spread.stats?.cv_pct != null && (
                <p className="text-xs text-gray-500 mt-2">
                  Variability {data.transit_spread.stats.cv_pct}% of the mean ·
                  median {data.transit_spread.stats.median} h ·
                  90th percentile {data.transit_spread.stats.p90} h — one trip in ten
                  takes at least that long.
                </p>
              )}
            </ChartCard>
            <ChartCard title="Detention spread"
        method={{
          formula: "Distribution of hours this carrier's trucks spent waiting at the plant.",
          plot: "histogram",
          caveat: "A long tail here is your cost, not the carrier's — it is usually a loading-bay problem.",
        }} icon={Clock} iconColor="text-amber-400"
              explain="Time this carrier's trucks spend waiting at the plant. A long tail here is your cost, not theirs — and it is usually a loading-bay problem rather than a carrier problem.">
              <HistogramChart bins={data.detention_spread.histogram} color={tc(tc('#f59e0b'))}
                mean={data.detention_spread.stats?.mean} median={data.detention_spread.stats?.median} />
            </ChartCard>
          </div>

          <ChartCard title="Departure rhythm — when this carrier loads"
        method={{
          formula: "A 7 × 24 grid counting this carrier's trips by the weekday and hour they departed.",
          plot: "heatmap",
          caveat: "A carrier whose band sits an hour before yours is one you can shift loading onto.",
        }}
            icon={CalendarClock} iconColor="text-blue-400"
            explain="Rows are weekdays, columns are the hour a trip left the plant, and each cell counts departures in that slot. Read it for the shape, not the totals: a dark vertical band is a gate rush that queues trucks against each other, an empty column is capacity nobody is using, and a carrier whose band sits an hour before yours is one you can shift loading onto.">
            <MatrixHeatmap rows={data.rhythm.rows} cols={data.rhythm.cols} values={data.rhythm.values}
              scheme="blues" cellW={30} cellH={26} labelW={54}
              valueFormatter={(v) => (v === 0 ? '' : v.toFixed(0))} />
          </ChartCard>
        </>
      )}

      {/* ------------------------------ network ------------------------------ */}
      {tab === 'network' && (
        <>
          <div className="grid lg:grid-cols-2 gap-6 mb-6">
            <ChartCard title="States served"
        method={{
          formula: "This carrier's trips grouped by destination state (from the consignee PIN), coloured by on-time rate.",
          plot: "hbar",
        }} icon={MapPin} iconColor="text-cyan-400"
              explain="Where this carrier's freight actually lands, from the consignee PIN. Coloured by on-time rate — a red bar is a region this carrier should not be given more of.">
              <HBarChart data={data.states.slice(0, 12)} nameKey="state" valueKey="trips"
                valueLabel="Trips" colorByKey="otd_pct" colorLo={50} colorHi={100} />
            </ChartCard>
            <ChartCard title="Regional service"
        method={{
          formula: "The same states as a table, with transit and distance.",
          plot: "table",
          caveat: "Useful when one bad region is dragging the headline on-time rate down.",
        }} icon={Gauge} iconColor="text-emerald-400"
              explain="The same states as a table, with transit and distance — useful when a single bad region is dragging the headline OTD down.">
              <div className="max-h-[320px] overflow-y-auto">
                <DataTable columns={[
                  { key: 'state', label: 'State' },
                  { key: 'trips', label: 'Trips' },
                  { key: 'otd_pct', label: 'OTD %',
                    render: (r: StateRow) => r.otd_pct != null
                      ? <span className={r.otd_pct >= 95 ? 'text-emerald-400' : r.otd_pct >= 80 ? 'text-amber-400' : 'text-red-400'}>{r.otd_pct}%</span>
                      : '—' },
                  { key: 'avg_transit_hours', label: 'Transit', render: (r: StateRow) => hh(r.avg_transit_hours) },
                  { key: 'total_km', label: 'Km', render: (r: StateRow) => formatNumber(r.total_km) },
                ]} data={data.states} emptyMessage="No destination resolved to a state." />
              </div>
            </ChartCard>
          </div>

          <ChartCard title="Lanes"
        method={{
          formula: "Per origin→destination pair. ‘Vs plan’ is mean actual transit minus mean quoted transit on that lane.",
          plot: "table",
          caveat: "Positive means the carrier breaks its OWN promise on that lane, rather than the lane simply being long.",
        }} icon={RouteIcon} iconColor="text-blue-400"
            explain='"Vs plan" is measured against this carrier’s own promised ETA on that lane, so a positive number is a promise it breaks rather than a route that is simply long.'
            actions={
              <button onClick={() => downloadCsv(data.lanes, `${carrier}-lanes.csv`)}
                className="px-3 py-1.5 rounded-lg text-xs text-gray-300 border border-gray-700 bg-gray-800 hover:text-white">
                Export CSV
              </button>
            }>
            <div className="max-h-[460px] overflow-y-auto">
              <DataTable columns={[
                { key: 'lane', label: 'Lane' },
                { key: 'trips', label: 'Trips' },
                { key: 'otd_pct', label: 'OTD %',
                  render: (r: any) => r.otd_pct != null
                    ? <span className={r.otd_pct >= 95 ? 'text-emerald-400' : r.otd_pct >= 80 ? 'text-amber-400' : 'text-red-400'}>{r.otd_pct}%</span>
                    : '—' },
                { key: 'avg_transit_hours', label: 'Transit', render: (r: any) => hh(r.avg_transit_hours) },
                { key: 'schedule_variance_hours', label: 'Vs plan',
                  render: (r: any) => r.schedule_variance_hours != null
                    ? <span className={r.schedule_variance_hours > 0 ? 'text-red-400' : 'text-emerald-400'}>
                        {r.schedule_variance_hours > 0 ? '+' : ''}{r.schedule_variance_hours.toFixed(1)} h</span>
                    : '—' },
                { key: 'avg_detention_hours', label: 'Detention', render: (r: any) => hh(r.avg_detention_hours) },
                { key: 'avg_distance_km', label: 'Avg km' },
              ]} data={data.lanes} />
            </div>
          </ChartCard>
        </>
      )}

      {/* ---------------------------- fleet & drivers ------------------------ */}
      {tab === 'fleet' && (
        <>
          <ChartCard title="Drivers"
        method={{
          formula: "Every driver this carrier has run for you, aggregated over the filtered trips.",
          plot: "table",
          caveat: "Use it to tell a carrier problem from a crew problem: one driver with poor on-time across many trips is a coaching conversation.",
        }} icon={Users} iconColor="text-purple-400" className="mb-6"
            explain="Every driver this carrier has run for you, busiest first. Use it to check whether a service problem is the carrier or one crew — a single driver with poor OTD across many trips is a coaching conversation, not a contract one."
            actions={
              <button onClick={() => downloadCsv(data.drivers, `${carrier}-drivers.csv`)}
                className="px-3 py-1.5 rounded-lg text-xs text-gray-300 border border-gray-700 bg-gray-800 hover:text-white">
                Export CSV
              </button>
            }>
            <div className="max-h-[460px] overflow-y-auto">
              <DataTable columns={[
                { key: 'name', label: 'Driver' },
                { key: 'trips', label: 'Trips' },
                { key: 'otd_pct', label: 'OTD %',
                  render: (r: DriverRow) => r.otd_pct != null
                    ? <span className={r.otd_pct >= 95 ? 'text-emerald-400' : r.otd_pct >= 80 ? 'text-amber-400' : 'text-red-400'}>{r.otd_pct}%</span>
                    : '—' },
                { key: 'avg_transit_hours', label: 'Transit', render: (r: DriverRow) => hh(r.avg_transit_hours) },
                { key: 'avg_speed_kmph', label: 'Avg speed',
                  render: (r: DriverRow) => r.avg_speed_kmph != null ? `${r.avg_speed_kmph} km/h` : '—' },
                { key: 'violations_per_trip', label: 'Provider pings/trip' },
                { key: 'vehicles', label: 'Vehicles' },
                { key: 'destinations', label: 'Dests' },
                { key: 'total_km', label: 'Km', render: (r: DriverRow) => formatNumber(r.total_km) },
                { key: 'last_trip', label: 'Last trip' },
              ]} data={data.drivers} emptyMessage="No driver names recorded for this carrier." />
            </div>
          </ChartCard>

          <ChartCard title="Vehicles"
        method={{
          formula: "Every truck deployed, aggregated over the filtered trips.",
          plot: "table",
          caveat: "A long list against few trips each is a market-hire pattern.",
        }} icon={Truck} iconColor="text-cyan-400" className="mb-6"
            explain="The trucks actually deployed. A long list against few trips each is a market-hire pattern: the carrier is brokering rather than running its own fleet, which is worth knowing before holding it to a fleet-quality standard."
            actions={
              <button onClick={() => downloadCsv(data.vehicles, `${carrier}-vehicles.csv`)}
                className="px-3 py-1.5 rounded-lg text-xs text-gray-300 border border-gray-700 bg-gray-800 hover:text-white">
                Export CSV
              </button>
            }>
            <div className="max-h-[460px] overflow-y-auto">
              <DataTable columns={[
                { key: 'vehicle_no', label: 'Vehicle',
                  render: (r: VehicleRow) => (
                    <Link to={`/vehicles/${encodeURIComponent(r.vehicle_no)}`}
                      className="text-blue-400 hover:text-blue-300">{r.vehicle_no}</Link>) },
                { key: 'vehicle_category', label: 'Type' },
                { key: 'own_market', label: 'Own/market' },
                { key: 'trips', label: 'Trips' },
                { key: 'otd_pct', label: 'OTD %',
                  render: (r: VehicleRow) => r.otd_pct != null ? `${r.otd_pct}%` : '—' },
                { key: 'avg_speed_kmph', label: 'Avg speed',
                  render: (r: VehicleRow) => r.avg_speed_kmph != null ? `${r.avg_speed_kmph} km/h` : '—' },
                { key: 'avg_gps_uptime', label: 'GPS %',
                  render: (r: VehicleRow) => r.avg_gps_uptime != null
                    ? <span className={r.avg_gps_uptime < 80 ? 'text-amber-400' : 'text-gray-300'}>{r.avg_gps_uptime}%</span>
                    : '—' },
                { key: 'violations_per_trip', label: 'Alerts/trip' },
                { key: 'total_km', label: 'Km', render: (r: VehicleRow) => formatNumber(r.total_km) },
                { key: 'last_trip', label: 'Last trip' },
              ]} data={data.vehicles} />
            </div>
          </ChartCard>

          <div className="grid lg:grid-cols-2 gap-6">
            <ChartCard title="Who this carrier hauls for"
        method={{
          formula: "Trips split by consignor.",
          plot: "donut",
        }} icon={Users} iconColor="text-emerald-400"
              explain="Consignor mix — a carrier serving one consignor exclusively behaves differently from one splitting capacity across several.">
              <DonutChart height={250}
                data={data.consignor_mix.slice(0, 8).map(m => ({ name: m.name, value: m.trips }))} />
            </ChartCard>
            <ChartCard title="Tracking hardware"
        method={{
          formula: "Trips split by GPS device type.",
          plot: "donut",
          caveat: "Where tracking quality is poor, this is usually where the answer is.",
        }} icon={Satellite} iconColor="text-blue-400"
              explain="Device type mix. Where GPS quality is poor, this is usually where the answer is.">
              <DonutChart height={250}
                data={data.device_type.map(m => ({ name: m.name, value: m.trips }))} />
            </ChartCard>
          </div>
        </>
      )}

      {/* --------------------------- tracking & safety ----------------------- */}
      {tab === 'tracking' && (
        <>
          <ChartCard title="GPS ping report quality"
        method={{
          formula: "Share of this carrier's trips tracked cleanly: pinged at all, first ping within 30 min of departure, uptime above 80%, and did not go dark at the plant. The band is a 95% Wilson score interval on that proportion.",
          plot: "table",
          caveat: "Rank on the interval, not the percentage. A carrier with five trips at 100% is not measurably better than one with two hundred at 92%.",
        }} icon={Satellite} iconColor="text-blue-400"
            className="mb-6"
            explain="How well this carrier's trackers actually reported. Every other number on this page rests on it: a carrier with dark trackers cannot be shown to be safe or on time, only unmeasured.">
            <GpsQualityPanel q={data.gps_quality} />
          </ChartCard>

          {mySafety && (
            <ChartCard title="Speeding"
        method={{
          formula: "Episodes, and the exact share of moving time over the limit, at the DEFAULT limits (60 km/h road, 20 km/h in-plant) so carrier pages stay comparable. Rated over trips that produced GPS.",
          plot: "table",
          caveat: "Change the limits on the Speed & Safety page; this panel deliberately does not follow them.",
        }} icon={ShieldAlert} iconColor="text-red-400" className="mb-6"
              explain="Judged at the default limits — 60 km/h on the road, 20 km/h inside a plant fence. Change the limits on the Speed & Safety page; this panel always shows the defaults so carrier pages stay comparable.">
              <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-7 gap-3">
                <Stat label="Episodes" value={mySafety.episodes}
                  tone={mySafety.episodes ? 'text-red-400' : 'text-emerald-400'} />
                <Stat label="Per 100 trips" value={mySafety.episodes_per_100_trips ?? '—'} />
                <Stat label="On the road" value={mySafety.road_episodes} />
                <Stat label="Inside plant" value={mySafety.plant_episodes} />
                <Stat label="Fastest seen"
                  value={mySafety.max_kmph != null ? `${mySafety.max_kmph} km/h` : '—'} />
                <Stat label="Avg moving speed"
                  value={mySafety.avg_moving_kmph != null ? `${mySafety.avg_moving_kmph} km/h` : '—'} />
                <Stat label="Over-limit driving"
                  value={pct(mySafety.over_pct_of_moving_time)} sub="share of moving time" />
              </div>
              <p className="text-xs text-gray-500 mt-3">
                Rated over {mySafety.gps_trips} of {mySafety.trips} trips — the ones that
                produced GPS. Trips with no ping are excluded rather than counted as clean.
              </p>
            </ChartCard>
          )}

          <ChartCard title="Running pattern — this carrier across 24 hours"
        method={{
          formula: "Wall minutes this carrier's trucks spent moving versus parked at each hour of the clock, summed across its trips.",
          plot: "stackedBar",
          caveat: "Not when its trips depart — that is the rhythm heatmap above.",
        }}
            icon={Activity} iconColor="text-emerald-400" className="mb-6"
            explain="What this carrier's trucks are doing at each hour of the clock: moving or parked. Not when its trips depart — that is the rhythm heatmap on the Performance tab — but what the fleet already on the road is doing."
            actions={pattern?.summary?.night_share_pct != null && (
              <span className="text-xs text-indigo-300 bg-indigo-950/50 border border-indigo-900 rounded-lg px-2.5 py-1">
                {pattern.summary.night_share_pct}% of driving at night
              </span>
            )}>
            {pattern?.hours?.length ? (
              <>
                <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
                  <Stat label="Busiest hour" value={pattern.summary.peak_hour ?? '—'}
                    sub={`${pattern.summary.peak_running_pct ?? '—'}% of trucks rolling`} />
                  <Stat label="Quietest hour" value={pattern.summary.quiet_hour ?? '—'}
                    sub={`${pattern.summary.quiet_running_pct ?? '—'}% rolling`} />
                  <Stat label="Moving hours"
                    value={formatNumber(pattern.summary.total_moving_hours)} />
                  <Stat label="Night driving"
                    value={formatNumber(pattern.summary.night_moving_hours)}
                    sub={pattern.summary.night_window ?? undefined} />
                </div>
                <BarChart data={patternBars} xKey="label" height={280} stacked showLegend series={[
                  { key: 'moving', label: 'Moving (h)', color: '#22c55e' },
                  { key: 'stopped', label: 'Parked (h)', color: tc('#4b5563') },
                ]} />
              </>
            ) : <p className="text-gray-500 text-sm py-6">No GPS in this window for this carrier.</p>}
          </ChartCard>

          <ChartCard title="Violation register"
        method={{
          formula: "This carrier's overspeed episodes at the default limits, worst peak first.",
          plot: "table",
        }} icon={AlertTriangle} iconColor="text-red-400"
            explain="This carrier's overspeed episodes at the default limits, worst peak first.">
            <div className="max-h-[420px] overflow-y-auto">
              <DataTable columns={[
                { key: 'start', label: 'When' },
                { key: 'trip_id', label: 'Trip',
                  render: (r: SpeedEvent) => (
                    <Link to={`/trips/${r.trip_id}`} className="text-blue-400 hover:text-blue-300">
                      {r.trip_id}</Link>) },
                { key: 'vehicle_no', label: 'Vehicle' },
                { key: 'driver_name', label: 'Driver' },
                { key: 'zone', label: 'Zone',
                  render: (r: SpeedEvent) => (
                    <Badge variant={r.zone === 'plant' ? 'neutral' : 'info'}
                      label={r.zone === 'plant' ? 'In plant' : 'Road'} />) },
                { key: 'peak_kmph', label: 'Peak',
                  render: (r: SpeedEvent) => (
                    <span className="text-red-400 font-semibold">{r.peak_kmph} km/h</span>) },
                { key: 'destination', label: 'To' },
              ]} data={violations?.rows ?? []}
                emptyMessage="No overspeed episode at the default limits." />
            </div>
          </ChartCard>
        </>
      )}

      {/* -------------------------------- trips ------------------------------ */}
      {tab === 'trips' && (
        <>
          <ChartCard title="Worst delivery slips"
        method={{
          formula: "Trips that arrived late, ordered by how many hours late.",
          plot: "table",
          caveat: "This is the evidence for a carrier review — an average is arguable, a named late trip is not.",
        }} icon={AlertTriangle} iconColor="text-red-400"
            className="mb-6"
            explain="The trips that actually broke a promise, longest slip first. This is the evidence to bring to a carrier review — an average is arguable, a named late trip is not.">
            <div className="max-h-[400px] overflow-y-auto">
              <DataTable columns={[
                { key: 'trip_id', label: 'Trip',
                  render: (r: any) => (
                    <Link to={`/trips/${r.trip_id}`} className="text-blue-400 hover:text-blue-300">
                      {r.trip_id}</Link>) },
                { key: 'dept_dt', label: 'Departed', render: (r: any) => formatDateTime(r.dept_dt) },
                { key: 'destination', label: 'To' },
                { key: 'vehicle_no', label: 'Vehicle' },
                { key: 'driver_name', label: 'Driver' },
                { key: 'delivery_delta_hours', label: 'Late by',
                  render: (r: any) => (
                    <span className="text-red-400 font-semibold">{r.delivery_delta_hours} h</span>) },
                { key: 'transit_hours', label: 'Transit', render: (r: any) => hh(r.transit_hours) },
                { key: 'detention_hours', label: 'Detention', render: (r: any) => hh(r.detention_hours) },
              ]} data={data.risk_trips} emptyMessage="No trip in this window delivered late 🎉" />
            </div>
          </ChartCard>

          <ChartCard title="Recent trips"
        method={{
          formula: "The 25 most recent trips this carrier ran inside the filter.",
          plot: "table",
        }} icon={Truck} iconColor="text-blue-400"
            explain="The last 25 trips this carrier ran in the filtered window.">
            <div className="max-h-[460px] overflow-y-auto">
              <DataTable columns={[
                { key: 'trip_id', label: 'Trip',
                  render: (r: any) => (
                    <Link to={`/trips/${r.trip_id}`} className="text-blue-400 hover:text-blue-300">
                      {r.trip_id}</Link>) },
                { key: 'dept_dt', label: 'Departed', render: (r: any) => formatDateTime(r.dept_dt) },
                { key: 'destination', label: 'To' },
                { key: 'vehicle_no', label: 'Vehicle' },
                { key: 'own_market', label: 'Fleet' },
                { key: 'transit_hours', label: 'Transit', render: (r: any) => hh(r.transit_hours) },
                { key: 'distance_km', label: 'Km' },
                { key: 'delivery_status', label: 'Outcome',
                  render: (r: any) => r.is_on_time == null ? '—' : (
                    <Badge variant={r.is_on_time ? 'success' : 'danger'}
                      label={r.delivery_status ?? (r.is_on_time ? 'On time' : 'Delayed')} />) },
              ]} data={data.recent_trips} />
            </div>
          </ChartCard>
        </>
      )}
    </PageContainer>
  );
}
