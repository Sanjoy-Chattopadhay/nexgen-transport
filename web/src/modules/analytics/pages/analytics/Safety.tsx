import { useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  AlertTriangle, Factory, Gauge, Clock, Truck, ShieldAlert, Activity,
  Moon, TrendingDown, Download, RefreshCw,
} from 'lucide-react';
import PageContainer from '../../components/layout/PageContainer';
import Spinner from '../../components/ui/Spinner';
import ChartCard from '../../components/ui/ChartCard';
import DataTable from '../../components/ui/DataTable';
import KPICard from '../../components/ui/KPICard';
import { ProofGrid } from '../../../../core/proof/ProofPanel';
import Badge from '../../components/ui/Badge';
import DualAxisChart from '../../components/charts/DualAxisChart';
import BarChart from '../../components/charts/BarChart';
import HBarChart from '../../components/charts/HBarChart';
import SpeedHistogram from '../../components/charts/SpeedHistogram';
import { useApi } from '../../hooks/useApi';
import { useTTAFilters } from '../../components/tta/dashboard/FilterContext';
import {
  getSpeedOverview, getSafetyDaily, getSpeedEvents, getCarrierSafety,
  getRunningPattern, type ZoneBlock, type SpeedEvent, type CarrierSafetyRow,
} from '../../services/speed';
import { formatNumber } from '../../lib/formatters';
import { downloadCsv } from '../../lib/csv';
import { tc } from '../../../../core/theme';

const ROAD_OPTIONS = [40, 50, 60, 70, 80];
const PLANT_OPTIONS = [10, 15, 20, 25, 30];

const hrs = (min: number | null | undefined) =>
  min == null ? '—' : min >= 60 ? `${(min / 60).toFixed(1)} h` : `${min.toFixed(0)} min`;

/** One zone's headline card: how fast, and how much of it was over the line. */
function ZoneCard({ zone, icon: Icon, tone }: {
  zone: ZoneBlock; icon: typeof Factory; tone: 'cyan' | 'violet';
}) {
  const accent = tone === 'cyan' ? 'text-cyan-400' : 'text-violet-400';
  const over = zone.over_pct_of_moving_time;
  return (
    <div className="bg-gray-900 rounded-xl border border-gray-800 p-5">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-sm font-semibold text-white flex items-center gap-2">
          <Icon className={`w-[18px] h-[18px] ${accent}`} /> {zone.label}
        </h3>
        <Badge variant={over != null && over > 5 ? 'danger' : over ? 'warning' : 'success'}
          label={`limit ${zone.limit_kmph} km/h`} />
      </div>
      <div className="grid grid-cols-3 gap-3 mb-4">
        {[
          ['Avg moving', zone.avg_moving_kmph, 'km/h'],
          ['85th pct', zone.p85_kmph, 'km/h'],
          ['Fastest', zone.max_kmph, 'km/h'],
        ].map(([label, v, unit]) => (
          <div key={label as string}>
            <p className="text-xs text-gray-500">{label as string}</p>
            <p className="text-lg font-bold text-gray-200">
              {v != null ? `${v}` : '—'}
              <span className="text-xs text-gray-500 ml-1">{unit as string}</span>
            </p>
          </div>
        ))}
      </div>
      {zone.fences && zone.fences.count > 0 && (
        <p className="text-xs text-amber-300/80 bg-amber-950/20 border border-amber-900/50 rounded-lg px-2.5 py-2 mb-3 leading-relaxed">
          &ldquo;Inside plant&rdquo; means within {zone.fences.radius_km} km of{' '}
          {zone.fences.count} published plant coordinate{zone.fences.count > 1 ? 's' : ''} —
          the radius the business specified. That circle contains public road as well
          as the works, so judging it against a yard limit will flag ordinary road
          driving near the plant. Treat these as places to look, not as proven
          yard-speeding.
        </p>
      )}
      <div className="grid grid-cols-2 gap-3 text-xs border-t border-gray-800 pt-3">
        <div>
          <p className="text-gray-500">Time over the limit</p>
          <p className="text-gray-200 font-semibold">
            {hrs(zone.over_minutes)}
            {over != null && <span className="text-gray-500 font-normal"> · {over}% of driving</span>}
          </p>
        </div>
        <div>
          <p className="text-gray-500">Distance over the limit</p>
          <p className="text-gray-200 font-semibold">{formatNumber(zone.over_dist_km)} km</p>
        </div>
        <div>
          <p className="text-gray-500">Violation episodes</p>
          <p className="text-gray-200 font-semibold">
            {zone.episodes}
            <span className="text-gray-500 font-normal"> · {zone.episode_vehicles} vehicles</span>
          </p>
        </div>
        <div>
          <p className="text-gray-500">Driving time measured</p>
          <p className="text-gray-200 font-semibold">{hrs(zone.moving_minutes)}</p>
        </div>
      </div>
    </div>
  );
}

export default function Safety() {
  const { params, paramsKey } = useTTAFilters();
  const [roadLimit, setRoadLimit] = useState(60);
  const [plantLimit, setPlantLimit] = useState(20);
  const [zoneFilter, setZoneFilter] = useState<'' | 'plant' | 'road'>('');
  const [hideStopped, setHideStopped] = useState(true);

  const limitKey = `${roadLimit}|${plantLimit}`;
  // The proofs are judged against the same two limits as every tile here.
  const proofParams = { ...params, road_limit: roadLimit, plant_limit: plantLimit };

  const { data: ov, loading } = useApi(
    () => getSpeedOverview(roadLimit, plantLimit, params), [paramsKey, limitKey]);
  const { data: daily } = useApi(
    () => getSafetyDaily(roadLimit, plantLimit, params), [paramsKey, limitKey]);
  const { data: carriers } = useApi(
    () => getCarrierSafety(roadLimit, plantLimit, 1, params), [paramsKey, limitKey]);
  const { data: pattern } = useApi(() => getRunningPattern(params), [paramsKey]);
  const { data: register } = useApi(
    () => getSpeedEvents(roadLimit, plantLimit, params, { zone: zoneFilter, rows: 300 }),
    [paramsKey, limitKey, zoneFilter]);

  const plant = ov?.zones.plant;
  const road = ov?.zones.road;
  const build = ov?.build;

  // The register was never rebuilt, so every zero on this page is "not measured"
  // rather than "no violations" — a distinction worth a banner, not a footnote.
  const notBuilt = build && !build.built;
  const overLimitCeiling = ov?.coverage.observed_max_kmph != null
    && roadLimit >= ov.coverage.observed_max_kmph;

  const carrierRows = useMemo(
    () => (carriers?.rows ?? []).filter(r => r.gps_trips > 0), [carriers]);

  const patternBars = useMemo(
    () => (pattern?.hours ?? []).map(h => ({
      label: h.label,
      moving: h.moving_hours ?? 0,
      stopped: h.stopped_hours ?? 0,
      running_pct: h.running_pct,
      in_plant: h.in_plant_hours ?? 0,
      avg_kmph: h.avg_kmph,
      vehicles: h.vehicles,
    })), [pattern]);

  return (
    <PageContainer title="🛡️ Speed & Safety">
      {/* ---------------- the control that governs the whole page ---------- */}
      <div className="bg-gray-900 rounded-xl border border-gray-800 p-5 mb-6">
        <div className="flex flex-wrap items-end gap-6">
          <div>
            <p className="text-xs text-gray-400 mb-1.5 flex items-center gap-1.5">
              <Gauge className="w-3.5 h-3.5 text-amber-400" /> Speed limit — outside plant
            </p>
            <div className="flex gap-1.5">
              {ROAD_OPTIONS.map(v => (
                <button key={v} onClick={() => setRoadLimit(v)}
                  className={`px-3 py-1.5 rounded-lg text-xs font-semibold border transition-colors ${
                    roadLimit === v
                      ? 'bg-amber-500/20 border-amber-500 text-amber-300'
                      : 'bg-gray-800 border-gray-700 text-gray-400 hover:text-gray-200'}`}>
                  {v}
                </button>
              ))}
              <span className="text-xs text-gray-600 self-center ml-1">km/h</span>
            </div>
          </div>
          <div>
            <p className="text-xs text-gray-400 mb-1.5 flex items-center gap-1.5">
              <Factory className="w-3.5 h-3.5 text-violet-400" /> Speed limit — inside plant
            </p>
            <div className="flex gap-1.5">
              {PLANT_OPTIONS.map(v => (
                <button key={v} onClick={() => setPlantLimit(v)}
                  className={`px-3 py-1.5 rounded-lg text-xs font-semibold border transition-colors ${
                    plantLimit === v
                      ? 'bg-violet-500/20 border-violet-500 text-violet-300'
                      : 'bg-gray-800 border-gray-700 text-gray-400 hover:text-gray-200'}`}>
                  {v}
                </button>
              ))}
              <span className="text-xs text-gray-600 self-center ml-1">km/h</span>
            </div>
          </div>
          <p className="text-xs text-gray-500 max-w-md leading-relaxed">
            Every figure on this page is recomputed against these two limits. Yard
            movement is judged separately from highway running — against a single
            60 km/h bar, no truck ever breaks an in-plant limit.
          </p>
        </div>
      </div>

      {notBuilt && (
        <div className="bg-amber-950/40 border border-amber-800 rounded-xl p-4 mb-6 flex items-start gap-3">
          <RefreshCw className="w-4 h-4 text-amber-400 mt-0.5 shrink-0" />
          <div className="text-xs text-amber-200">
            <p className="font-semibold mb-0.5">Speed rollups have never been built.</p>
            <p className="text-amber-300/80">
              Everything below reads as zero because nothing has been measured yet, not
              because nothing happened. Run <code className="text-amber-200">POST /api/v1/speed/rebuild</code> once
              to scan the ping history (a few minutes), then reload.
            </p>
          </div>
        </div>
      )}

      {!notBuilt && build?.stale && (
        <div className="bg-gray-900 border border-gray-800 rounded-xl px-4 py-2.5 mb-6 text-xs text-gray-400">
          Measured to <span className="text-gray-200">{build.built_at}</span> ·{' '}
          {formatNumber(build.pings_covered)} of {formatNumber(build.gps_pings)} GPS pings
          scanned. {formatNumber(build.pings_behind)} newer pings are not in these figures yet.
        </div>
      )}

      {loading ? <Spinner /> : ov && (
        <>
          <ProofGrid className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
            <KPICard label="Violation episodes" value={formatNumber(ov.totals.episodes)}
              proof={{ dataset: 'safety.episodes', params: proofParams, value: ov.totals.episodes }}
              icon={ShieldAlert} color={ov.totals.episodes ? 'red' : 'green'} />
            <KPICard label="Violations / day"
              value={ov.totals.episodes_per_day ?? '—'}
              proof={{ dataset: 'safety.perday', params: proofParams, value: ov.totals.episodes_per_day ?? null }}
              icon={AlertTriangle} color="amber" />
            <KPICard label="Time over the limit" value={hrs(ov.totals.over_minutes)}
              proof={{ dataset: 'safety.overmin', params: proofParams, value: ov.totals.over_minutes }}
              icon={Clock} color="amber" />
            <KPICard label="Distance over the limit"
              value={`${formatNumber(ov.totals.over_dist_km)} km`}
              proof={{ dataset: 'safety.overkm', params: proofParams, value: ov.totals.over_dist_km }}
              icon={TrendingDown} color="red" />
          </ProofGrid>

          <p className="text-xs text-gray-500 mb-6 -mt-2">
            Measured across {formatNumber(ov.coverage.trips_with_gps)} of{' '}
            {formatNumber(ov.coverage.trips_in_filter)} filtered trips
            ({ov.coverage.gps_coverage_pct ?? 0}% produced GPS). Trips with no ping cannot
            be judged on speed and are excluded rather than counted as clean.
            {ov.coverage.observed_max_kmph != null &&
              ` Fastest ping anywhere in this window: ${ov.coverage.observed_max_kmph} km/h.`}
          </p>

          {overLimitCeiling && (
            <div className="bg-gray-900 border border-gray-800 rounded-xl px-4 py-3 mb-6 text-xs text-gray-400">
              No ping in this window exceeded{' '}
              <span className="text-gray-200">{ov.coverage.observed_max_kmph} km/h</span>, so a{' '}
              {roadLimit} km/h limit reports zero road violations by construction — these
              trucks are speed-limited. Lower the limit to see where the fleet actually sits.
            </div>
          )}

          {/* ------------------- in-plant vs outside-plant ------------------ */}
          <h2 className="text-sm font-semibold text-gray-300 mb-3">
            Inside the plant vs out on the road
          </h2>
          <div className="grid lg:grid-cols-2 gap-6 mb-6">
            {plant && <ZoneCard zone={plant} icon={Factory} tone="violet" />}
            {road && <ZoneCard zone={road} icon={Truck} tone="cyan" />}
          </div>

          <div className="grid lg:grid-cols-2 gap-6 mb-6">
            <ChartCard title="Speed profile — inside plant"
        method={{
          formula: "Minutes at each exact km/h, for pings inside a gazetteer plant fence. Each ping is credited the wall time back to the previous ping of the same trip, capped at 15 min so a GPS outage is not billed to whatever speed the truck was last doing. Bars above your in-plant limit are red.",
          plot: "histogram",
          caveat: "“Inside plant” is a 10 km circle round a published plant coordinate — the radius the business specified. It contains public road, so traffic on it appears here.",
        }} icon={Factory} iconColor="text-violet-400"
              explain={`Minutes spent at each km/h inside a plant fence. Red is above your ${plantLimit} km/h works limit. Weighted by time, not by ping count, so a dense tracker does not read as a slower truck.`}
              actions={
                <button onClick={() => setHideStopped(v => !v)}
                  className="px-2.5 py-1 rounded-lg text-xs border border-gray-700 bg-gray-800 text-gray-400 hover:text-gray-200">
                  {hideStopped ? 'Show parked (0 km/h)' : 'Hide parked'}
                </button>
              }>
              {plant && <SpeedHistogram bins={plant.histogram} limit={plantLimit}
                hideStopped={hideStopped} color="#a78bfa" />}
            </ChartCard>
            <ChartCard title="Speed profile — outside plant"
        method={{
          formula: "The same 1 km/h histogram for every ping outside a plant fence. Time-weighted, not ping-counted: a tracker that reports twice a minute must not read as a truck that spent twice as long at that speed.",
          plot: "histogram",
          caveat: "The right-hand end of the tail is the fastest this fleet can physically run (76 km/h corpus-wide) — these trucks are speed-limited, so a limit above it yields zero violations by construction.",
        }} icon={Truck} iconColor="text-cyan-400"
              explain={`The same, on the road. Red is above your ${roadLimit} km/h limit. The right-hand tail is the whole safety conversation: where it ends is the fastest this fleet is physically able to run.`}>
              {road && <SpeedHistogram bins={road.histogram} limit={roadLimit}
                hideStopped={hideStopped} color="#38bdf8" />}
            </ChartCard>
          </div>

          {/* --------------------- violations per day ----------------------- */}
          <ChartCard title="⚠️ Safety violations per day"
        method={{
          formula: "Bars: overspeed EPISODES starting that day, split road vs in-plant. An episode is one continuous run of pings above the limit, so a 12-minute overspeed counts once, not once per ping. Line: episodes ÷ trips on the road that day × 100.",
          plot: "dualAxis",
          caveat: "The denominator is trips ACTIVE that day, not departing. A violation usually belongs to a trip that left days earlier, and dividing by that day's departures compares two different populations.",
        }} icon={AlertTriangle} iconColor="text-red-400"
            className="mb-6"
            explain="Bars are violation episodes — a continuous overspeed run counts once, not once per ping. The line normalises them per 100 trips ON THE ROAD that day, which is the number to read: 12 violations across 400 running trucks is a better day than 6 across 50. Exposure is trips actually running, not trips departing — a violation often belongs to a trip that left days earlier, and dividing by that day's departures would compare two different populations."
            actions={daily && (
              <button onClick={() => downloadCsv(daily.series, 'safety-daily.csv')}
                className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs text-gray-300 border border-gray-700 bg-gray-800 hover:text-white">
                <Download className="w-3.5 h-3.5" /> CSV
              </button>
            )}>
            {daily ? (
              <>
                <div className="grid grid-cols-2 md:grid-cols-5 gap-3 mb-4">
                  {[
                    ['Days in window', daily.summary.days],
                    ['Clean days', daily.summary.clean_days],
                    ['Avg per day', daily.summary.avg_per_day ?? '—'],
                    ['Worst day', daily.summary.worst_day
                      ? `${daily.summary.worst_day} (${daily.summary.worst_day_episodes})` : '—'],
                    ['Vehicles involved', daily.summary.offending_vehicles],
                  ].map(([l, v]) => (
                    <div key={l as string} className="bg-gray-950/60 border border-gray-800 rounded-lg px-3 py-2">
                      <p className="text-xs text-gray-500">{l as string}</p>
                      <p className="text-sm font-bold text-gray-200">{v as string}</p>
                    </div>
                  ))}
                </div>
                <DualAxisChart data={daily.series} xKey="date" height={300} series={[
                  { key: 'road_episodes', label: 'On the road', color: tc('#ef4444'), type: 'bar' },
                  { key: 'plant_episodes', label: 'Inside plant', color: '#a78bfa', type: 'bar' },
                  { key: 'episodes_per_100_trips', label: 'Per 100 trips', color: tc('#fbbf24'), axis: 'right' },
                ]} />
              </>
            ) : <Spinner />}
          </ChartCard>

          <div className="grid lg:grid-cols-2 gap-6 mb-6">
            <ChartCard title="Repeat offenders"
        method={{
          formula: "Vehicles ranked by episode count at the chosen limits. Peak km/h is the fastest single ping in any of that vehicle's episodes.",
          plot: "table",
        }} icon={Truck} iconColor="text-red-400"
              explain="Vehicles by number of violation episodes. A vehicle appearing here repeatedly is a driver-coaching or governor-tampering conversation, not a one-off.">
              <div className="max-h-[340px] overflow-y-auto">
                <DataTable columns={[
                  { key: 'vehicle_no', label: 'Vehicle' },
                  { key: 'transporter', label: 'Transporter',
                    render: (r: any) => <span className="text-gray-400 text-xs">{r.transporter}</span> },
                  { key: 'episodes', label: 'Episodes' },
                  { key: 'peak_kmph', label: 'Peak',
                    render: (r: any) => <span className="text-red-400 font-semibold">{r.peak_kmph} km/h</span> },
                  { key: 'plant_episodes', label: 'In plant' },
                ]} data={daily?.worst_offenders ?? []}
                  emptyMessage="No violations at these limits 🎉" />
              </div>
            </ChartCard>

            <ChartCard title="Violations per 100 trips, by carrier"
        method={{
          formula: "Episodes ÷ that carrier's trips WITH GPS × 100. Trips that produced no ping are excluded from the denominator rather than counted as clean.",
          plot: "hbar",
          caveat: "A carrier with dark trackers scores well here for having recorded nothing. Read gps_coverage_pct in the table below alongside it.",
        }} icon={ShieldAlert} iconColor="text-orange-400"
              explain="Rated against trips that actually produced GPS, not all trips — otherwise a carrier whose trackers are dark would top the safety table for having recorded nothing.">
              <HBarChart data={[...carrierRows].slice(0, 12)} nameKey="transporter"
                valueKey="episodes_per_100_trips" valueLabel="per 100 trips" color="#f97316" />
            </ChartCard>
          </div>

          {/* ------------------- 24-hour running pattern -------------------- */}
          <ChartCard title="🕒 Running pattern — the fleet across 24 hours"
        method={{
          formula: "For each hour of the clock, the wall minutes every in-scope trip spent moving (speed ≥ 1 km/h) versus parked, summed across trips and converted to hours. In-plant time is the same clock sliced by position, shown separately so it is never double-counted.",
          plot: "stackedBar",
          caveat: "This is what trucks already on the road are DOING at that hour — not when trips departed. That is the departure rhythm heatmap, a different question.",
        }} icon={Activity}
            iconColor="text-emerald-400" className="mb-6"
            explain="What the trucks already on the road are DOING at each hour: moving, parked, or sitting inside a plant. This is not the departure heatmap — that counts trips leaving, this measures the fleet already out there. Moving + parked fill the hour; in-plant is a slice of the same clock shown separately so it is never double-counted."
            actions={pattern?.summary?.night_share_pct != null && (
              <span className="flex items-center gap-1.5 text-xs text-indigo-300 bg-indigo-950/50 border border-indigo-900 rounded-lg px-2.5 py-1">
                <Moon className="w-3.5 h-3.5" />
                {pattern.summary.night_share_pct}% of driving in {pattern.summary.night_window}
              </span>
            )}>
            {pattern ? (
              <>
                <div className="grid grid-cols-2 md:grid-cols-5 gap-3 mb-4">
                  {[
                    ['Busiest hour', pattern.summary.peak_hour
                      ? `${pattern.summary.peak_hour} (${pattern.summary.peak_running_pct}%)` : '—'],
                    ['Quietest hour', pattern.summary.quiet_hour
                      ? `${pattern.summary.quiet_hour} (${pattern.summary.quiet_running_pct}%)` : '—'],
                    ['Avg wheels turning', pattern.summary.avg_running_pct != null
                      ? `${pattern.summary.avg_running_pct}%` : '—'],
                    ['Moving hours', formatNumber(pattern.summary.total_moving_hours)],
                    ['In-plant hours', formatNumber(pattern.summary.total_in_plant_hours)],
                  ].map(([l, v]) => (
                    <div key={l as string} className="bg-gray-950/60 border border-gray-800 rounded-lg px-3 py-2">
                      <p className="text-xs text-gray-500">{l as string}</p>
                      <p className="text-sm font-bold text-gray-200">{v as string}</p>
                    </div>
                  ))}
                </div>
                <BarChart data={patternBars} xKey="label" height={300} stacked showLegend series={[
                  { key: 'moving', label: 'Moving (h)', color: '#22c55e' },
                  { key: 'stopped', label: 'Parked (h)', color: tc('#4b5563') },
                ]} />
                <p className="text-xs text-gray-500 mt-3">
                  A tall grey block in working hours is capacity sitting idle; a tall green
                  block after midnight is night running, which is where fatigue incidents
                  concentrate. Read the two together — a fleet that parks all afternoon and
                  drives all night has a dispatch problem, not a driver problem.
                </p>
              </>
            ) : <Spinner />}
          </ChartCard>

          {/* ----------------------- carrier scorecard ---------------------- */}
          <ChartCard title="Safety by transporter"
        method={{
          formula: "Per carrier: episodes, episodes per 100 GPS trips, share of moving time over the limit (exact, from the speed histogram), and time-weighted average and peak speed.",
          plot: "table",
        }} icon={Truck} iconColor="text-amber-400"
            className="mb-6"
            explain={`Judged at ${roadLimit} km/h on the road and ${plantLimit} km/h inside a plant. "Over-limit driving" is the share of this carrier's moving time spent above its limit — exact, and independent of how many pings its trackers happen to send.`}
            actions={carriers && (
              <button onClick={() => downloadCsv(carriers.rows, 'carrier-safety.csv')}
                className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs text-gray-300 border border-gray-700 bg-gray-800 hover:text-white">
                <Download className="w-3.5 h-3.5" /> CSV
              </button>
            )}>
            <div className="max-h-[420px] overflow-y-auto">
              <DataTable columns={[
                { key: 'transporter', label: 'Transporter',
                  render: (r: CarrierSafetyRow) => (
                    <Link to={`/transporters/${encodeURIComponent(r.transporter)}`}
                      className="text-blue-400 hover:text-blue-300">{r.transporter}</Link>) },
                { key: 'gps_trips', label: 'Trips w/ GPS',
                  render: (r: CarrierSafetyRow) => (
                    <span>{r.gps_trips}<span className="text-gray-600 text-xs"> / {r.trips}</span></span>) },
                { key: 'episodes', label: 'Episodes' },
                { key: 'episodes_per_100_trips', label: 'Per 100 trips',
                  render: (r: CarrierSafetyRow) => r.episodes_per_100_trips != null
                    ? <span className={r.episodes_per_100_trips > 50 ? 'text-red-400 font-semibold'
                      : r.episodes_per_100_trips > 10 ? 'text-amber-400' : 'text-emerald-400'}>
                        {r.episodes_per_100_trips}</span> : '—' },
                { key: 'over_pct_of_moving_time', label: 'Over-limit driving',
                  render: (r: CarrierSafetyRow) => r.over_pct_of_moving_time != null
                    ? `${r.over_pct_of_moving_time}%` : '—' },
                { key: 'avg_moving_kmph', label: 'Avg speed',
                  render: (r: CarrierSafetyRow) => r.avg_moving_kmph != null ? `${r.avg_moving_kmph} km/h` : '—' },
                { key: 'max_kmph', label: 'Fastest',
                  render: (r: CarrierSafetyRow) => r.max_kmph != null ? `${r.max_kmph} km/h` : '—' },
                { key: 'plant_episodes', label: 'In plant' },
                { key: 'offending_vehicles', label: 'Vehicles' },
                { key: 'gps_coverage_pct', label: 'GPS %',
                  render: (r: CarrierSafetyRow) => r.gps_coverage_pct != null
                    ? <span className={r.gps_coverage_pct < 60 ? 'text-amber-400' : 'text-gray-300'}>
                        {r.gps_coverage_pct}%</span> : '—' },
              ]} data={carrierRows} emptyMessage="No carrier produced GPS in this window." />
            </div>
          </ChartCard>

          {/* ---------------------- violation register ---------------------- */}
          <ChartCard title="Violation register"
        method={{
          formula: "One row per overspeed episode. Peak and average km/h are exact; the duration is the span of continuous running above the DETECTION FLOOR that contains the excursion, not time above your chosen limit.",
          plot: "table",
          caveat: "Exact time above your limit is on the zone cards at the top of the page — it comes from the histogram, which the register cannot reproduce.",
        }} icon={AlertTriangle} iconColor="text-red-400"
            explain={`Every overspeed episode, worst peak first. An episode is one continuous run above the detection floor (${ov.zones.road.detection_floor_kmph} km/h on the road, ${ov.zones.plant.detection_floor_kmph} inside a plant); it is listed here because its peak breached your chosen limit. The duration is that whole run — exact time above your limit is on the cards above.`}
            actions={
              <div className="flex items-center gap-2">
                <select value={zoneFilter} onChange={e => setZoneFilter(e.target.value as any)}
                  className="bg-gray-800 border border-gray-700 rounded-lg px-2 py-1.5 text-xs text-gray-300 outline-none">
                  <option value="">Both zones</option>
                  <option value="road">Outside plant</option>
                  <option value="plant">Inside plant</option>
                </select>
                {register && (
                  <button onClick={() => downloadCsv(register.rows, 'violations.csv')}
                    className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs text-gray-300 border border-gray-700 bg-gray-800 hover:text-white">
                    <Download className="w-3.5 h-3.5" /> CSV
                  </button>
                )}
              </div>
            }>
            <div className="max-h-[460px] overflow-y-auto">
              <DataTable columns={[
                { key: 'start', label: 'When' },
                { key: 'trip_id', label: 'Trip',
                  render: (r: SpeedEvent) => (
                    <Link to={`/trips/${r.trip_id}`} className="text-blue-400 hover:text-blue-300">
                      {r.trip_id}</Link>) },
                { key: 'transporter', label: 'Transporter' },
                { key: 'vehicle_no', label: 'Vehicle' },
                { key: 'driver_name', label: 'Driver' },
                { key: 'zone', label: 'Zone',
                  render: (r: SpeedEvent) => (
                    <Badge variant={r.zone === 'plant' ? 'neutral' : 'info'}
                      label={r.zone === 'plant' ? 'In plant' : 'Road'} />) },
                { key: 'peak_kmph', label: 'Peak',
                  render: (r: SpeedEvent) => (
                    <span className="text-red-400 font-semibold">{r.peak_kmph} km/h</span>) },
                { key: 'avg_kmph', label: 'Avg' },
                { key: 'episode_minutes', label: 'Episode',
                  render: (r: SpeedEvent) => hrs(r.episode_minutes) },
                { key: 'dist_km', label: 'Km' },
                { key: 'destination', label: 'To' },
              ]} data={register?.rows ?? []}
                emptyMessage="No episode breached these limits." />
            </div>
            {register && register.total > register.returned && (
              <p className="text-xs text-gray-500 mt-3">
                Showing the {register.returned} worst of {formatNumber(register.total)} episodes.
              </p>
            )}
          </ChartCard>
        </>
      )}
    </PageContainer>
  );
}
