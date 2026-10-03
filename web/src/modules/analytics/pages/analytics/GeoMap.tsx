import { useMemo, useState } from 'react';
import {
  Map as MapIcon, MapPin, HelpCircle, Boxes, Gauge, BarChart3, Network,
} from 'lucide-react';
import PageContainer from '../../components/layout/PageContainer';
import Spinner from '../../components/ui/Spinner';
import ChartCard from '../../components/ui/ChartCard';
import DataTable from '../../components/ui/DataTable';
import HBarChart from '../../components/charts/HBarChart';
import DualAxisChart from '../../components/charts/DualAxisChart';
import GeoFlowMap, { type GeoPoint } from '../../components/tta/dashboard/GeoFlowMap';
import StateBubbleMap from '../../components/tta/dashboard/StateBubbleMap';
import { useApi } from '../../hooks/useApi';
import { useTTAFilters } from '../../components/tta/dashboard/FilterContext';
import { getDashGeo, getDashGeoStates, type StateRow } from '../../services/ttaDashboard';
import { formatNumber } from '../../lib/formatters';
import { downloadCsv } from '../../lib/csv';
import { tc } from '../../../../core/theme';

const COLOR_METRICS = [
  { key: 'otd_pct' as const, label: 'OTD %' },
  { key: 'avg_transit_hours' as const, label: 'Avg transit' },
  { key: 'avg_distance_km' as const, label: 'Avg distance' },
];

/** A state needs this many delivered trips before its OTD is treated as a
 *  finding rather than a coincidence. Mirrors the map's own grey-out rule. */
const MIN_JUDGED = 10;

export default function GeoMap() {
  const { params, paramsKey } = useTTAFilters();
  const [showArcs, setShowArcs] = useState(true);
  const [showBubbles, setShowBubbles] = useState(true);
  const [showHeat, setShowHeat] = useState(false);
  const [colorMetric, setColorMetric] = useState<'otd_pct' | 'avg_transit_hours' | 'avg_distance_km'>('otd_pct');

  const { data: geo, loading } = useApi(() => getDashGeo(params), [paramsKey]);
  const { data: st, loading: stLoading } = useApi(() => getDashGeoStates(params), [paramsKey]);

  const points: GeoPoint[] = geo?.points ?? [];
  const states = st?.states ?? [];

  // Volume ranking, and the service ranking limited to states we can actually
  // speak about. Sorting every state by OTD would put a 2-trip state at both
  // ends of the list and bury the ones that matter.
  const byVolume = useMemo(() => states.slice(0, 12), [states]);
  const judged = useMemo(
    () => states.filter(s => s.judged_trips >= MIN_JUDGED && s.otd_pct != null), [states]);
  const worstService = useMemo(
    () => [...judged].sort((a, b) => (a.otd_pct ?? 0) - (b.otd_pct ?? 0)).slice(0, 12), [judged]);

  // Volume against service on one axis pair — the states carrying real freight
  // AND missing dates are the ones to act on.
  const volumeVsService = useMemo(
    () => [...judged].sort((a, b) => b.trips - a.trips).slice(0, 15)
      .map(s => ({ state: s.state, trips: s.trips, otd_pct: s.otd_pct })),
    [judged]);

  return (
    <PageContainer title="🗺️ Geo Intelligence Map">
      {/* --------------------------- statewide view -------------------------- */}
      <div className="mb-2">
        <h2 className="text-sm font-semibold text-gray-300">Where the freight goes, and where it lands late</h2>
        <p className="text-xs text-gray-500 mt-1 max-w-3xl">
          Rolled up to state, from the consignee PIN. At destination grain this map was
          {' '}{geo ? formatNumber(geo.points.length + (geo.unmapped?.length ?? 0)) : '120+'}
          {' '}overlapping circles, most carrying one or two trips — too few for an on-time
          rate to mean anything and too crowded to read. A state aggregates enough trips
          to be worth a decision.
          {st && ` ${st.mapped_pct}% of trips resolve to a state.`}
        </p>
      </div>

      {stLoading ? <Spinner /> : st && (
        <>
          <div className="grid grid-cols-2 md:grid-cols-5 gap-3 my-4">
            {[
              ['States served', st.totals.states],
              ['Trips mapped', formatNumber(st.totals.mapped_trips)],
              ['Biggest state', st.totals.top_state ?? '—'],
              ['Top 3 states hold', `${st.totals.top3_share_pct ?? 0}%`],
              ['Fleet OTD', st.totals.otd_pct != null ? `${st.totals.otd_pct}%` : '—'],
            ].map(([l, v]) => (
              <div key={l as string} className="bg-gray-900 border border-gray-800 rounded-lg px-3 py-2">
                <p className="text-xs text-gray-500">{l as string}</p>
                <p className="text-sm font-bold text-gray-200 truncate">{v as string}</p>
              </div>
            ))}
          </div>

          <div className="grid lg:grid-cols-2 gap-6 mb-6">
            <ChartCard title="Volume bubble map"
        method={{
          formula: "Trips grouped by destination STATE, resolved from the consignee PIN (3-digit postal ranges, not the node name). Bubble area is trip count; the circle sits on the state centroid.",
          plot: "map",
        }} icon={Boxes} iconColor="text-cyan-400"
              explain="Bubble area is trips into that state — this is the demand map. The three biggest states usually carry most of the freight, which is what makes a service problem in one of them a network problem.">
              <StateBubbleMap states={states} origin={st.origin} mode="volume" height={420} />
            </ChartCard>
            <ChartCard title="On-time bubble map"
        method={{
          formula: "The same bubbles coloured by on-time rate: on-time trips ÷ trips with a recorded delivery status. Size still carries volume so a red pinprick is not mistaken for a crisis.",
          plot: "map",
          caveat: "States with fewer than 10 judged trips stay grey. Two late trips out of three is not a 33% service failure.",
        }} icon={Gauge} iconColor="text-emerald-400"
              explain={`Same bubbles, coloured green→red by on-time rate. Size still carries volume, so a big red circle is a real problem and a small one is a small one. States with fewer than ${MIN_JUDGED} delivered trips stay grey — two late trips out of three is not a 33% service failure.`}>
              <StateBubbleMap states={states} origin={st.origin} mode="ontime" height={420}
                minJudged={MIN_JUDGED} />
            </ChartCard>
          </div>

          <div className="grid lg:grid-cols-2 gap-6 mb-6">
            <ChartCard title="Freight by state"
        method={{
          formula: "Trip count per state, ranked. Bar colour is that state's on-time rate, so length and colour are two independent facts.",
          plot: "hbar",
        }} icon={BarChart3} iconColor="text-cyan-400"
              explain="The same volume map as a ranking, coloured by on-time rate. Read the long bars first: that is where a service change is worth most.">
              <HBarChart data={byVolume} nameKey="state" valueKey="trips" valueLabel="Trips"
                colorByKey="otd_pct" colorLo={60} colorHi={100} />
            </ChartCard>
            <ChartCard title="Worst-served states"
        method={{
          formula: "On-time rate ascending, over states with at least 10 delivered trips.",
          plot: "hbar",
          caveat: "Far states are structurally harder. Check average transit and distance in the table before treating a low bar as a carrier failure.",
        }} icon={MapPin} iconColor="text-red-400"
              explain={`On-time rate, worst first, over states with at least ${MIN_JUDGED} delivered trips. Check transit distance before blaming a carrier — the far states are structurally harder.`}>
              {worstService.length ? (
                <HBarChart data={worstService} nameKey="state" valueKey="otd_pct" valueLabel="OTD %"
                  colorByKey="otd_pct" colorLo={60} colorHi={100} />
              ) : (
                <p className="text-gray-500 text-sm py-8">
                  No state has {MIN_JUDGED} delivered trips in this window yet.
                </p>
              )}
            </ChartCard>
          </div>

          <ChartCard title="Volume against service"
        method={{
          formula: "Bars are trips per state; the line is that state's on-time rate on an independent right axis.",
          plot: "dualAxis",
        }} icon={BarChart3} iconColor="text-amber-400"
            className="mb-6"
            explain="Bars are trips, the line is on-time rate. A tall bar with a low line is the expensive combination: a lot of freight going somewhere it arrives late.">
            <DualAxisChart data={volumeVsService} xKey="state" height={300} rightDomain={[0, 100]}
              series={[
                { key: 'trips', label: 'Trips', color: tc('#06b6d4'), type: 'bar' },
                { key: 'otd_pct', label: 'OTD %', color: '#22c55e', axis: 'right' },
              ]} />
          </ChartCard>

          <ChartCard title="State detail"
        method={{
          formula: "Every figure behind the bubbles. ‘Judged’ is how many trips recorded a delivery status — the on-time rate is over those only. The 95% band is a Wilson score interval, which stays inside 0–100% on the small counts where the textbook interval does not.",
          plot: "table",
        }} icon={MapPin} iconColor="text-blue-400" className="mb-6"
            explain="Everything behind the bubbles. 'Judged' is how many of those trips actually recorded a delivery status — the on-time rate is only over those, and the confidence band shows how far it could be out."
            actions={
              <button onClick={() => downloadCsv(states, 'states.csv')}
                className="px-3 py-1.5 rounded-lg text-xs text-gray-300 border border-gray-700 bg-gray-800 hover:text-white">
                Export CSV
              </button>
            }>
            <div className="max-h-[420px] overflow-y-auto">
              <DataTable columns={[
                { key: 'state', label: 'State' },
                { key: 'trips', label: 'Trips' },
                { key: 'share_pct', label: 'Share',
                  render: (r: StateRow) => `${r.share_pct ?? 0}%` },
                { key: 'otd_pct', label: 'OTD %',
                  render: (r: StateRow) => r.otd_pct == null ? '—' : (
                    <span className={r.judged_trips < MIN_JUDGED ? 'text-gray-500'
                      : r.otd_pct >= 95 ? 'text-emerald-400'
                      : r.otd_pct >= 80 ? 'text-amber-400' : 'text-red-400'}>
                      {r.otd_pct}%
                    </span>) },
                { key: 'judged_trips', label: 'Judged',
                  render: (r: StateRow) => (
                    <span className={r.judged_trips < MIN_JUDGED ? 'text-amber-400' : 'text-gray-300'}>
                      {r.judged_trips}
                    </span>) },
                { key: 'otd_ci_low', label: '95% band',
                  render: (r: StateRow) => r.otd_ci_low == null ? '—'
                    : <span className="text-gray-500 text-xs">{r.otd_ci_low}–{r.otd_ci_high}%</span> },
                { key: 'avg_transit_hours', label: 'Transit',
                  render: (r: StateRow) => r.avg_transit_hours != null ? `${r.avg_transit_hours} h` : '—' },
                { key: 'avg_distance_km', label: 'Avg km' },
                { key: 'destinations', label: 'Dests' },
                { key: 'transporters', label: 'Carriers' },
              ]} data={states} />
            </div>
            {!!st.unmapped.length && (
              <p className="text-xs text-gray-500 mt-3">
                {st.unmapped.length} destination{st.unmapped.length > 1 ? 's' : ''} did not resolve
                to a state ({formatNumber(st.unmapped.reduce((a, u) => a + u.trips, 0))} trips) and
                are missing from every bubble above:{' '}
                {st.unmapped.slice(0, 5).map(u => u.destination).join(', ')}
                {st.unmapped.length > 5 && ' …'}
              </p>
            )}
          </ChartCard>
        </>
      )}

      {/* ------------------------- destination detail ------------------------ */}
      <h2 className="text-sm font-semibold text-gray-300 mb-1 mt-8">Plant-to-destination flows</h2>
      <p className="text-xs text-gray-500 mb-4 max-w-3xl">
        The point-level view, kept for tracing an individual lane. Destinations are placed
        by exact city match, falling back to a pin-prefix centroid — good enough to draw a
        flow, not precise enough to measure a distance from.
      </p>

      <div className="bg-gray-900 rounded-xl border border-gray-800 p-5 mb-6">
        <div className="flex items-center justify-between flex-wrap gap-3 mb-4">
          <h3 className="text-base font-semibold text-white flex items-center gap-2">
            <Network className="w-[18px] h-[18px] text-cyan-400" /> Outbound Flow Network
            {geo && <span className="text-xs text-gray-500 font-normal">{geo.mapped_pct}% drawable</span>}
          </h3>
          <div className="flex items-center gap-2 flex-wrap">
            {[
              { label: 'Flow arcs', v: showArcs, set: setShowArcs },
              { label: 'Volume bubbles', v: showBubbles, set: setShowBubbles },
              { label: 'Density heat', v: showHeat, set: setShowHeat },
            ].map(t => (
              <button key={t.label} onClick={() => t.set(x => !x)}
                className={`px-3 py-1.5 rounded-lg text-xs font-medium border transition-colors ${
                  t.v ? 'bg-blue-600/20 border-blue-600 text-blue-300' : 'bg-gray-800 border-gray-700 text-gray-500'}`}>
                {t.label}
              </button>
            ))}
            <select value={colorMetric} onChange={e => setColorMetric(e.target.value as any)}
              className="bg-gray-800 border border-gray-700 rounded-lg px-2 py-1.5 text-xs text-gray-300 outline-none">
              {COLOR_METRICS.map(m => <option key={m.key} value={m.key}>Colour: {m.label}</option>)}
            </select>
          </div>
        </div>
        {loading ? <Spinner /> : geo && (
          <GeoFlowMap origin={geo.origin} points={points} colorMetric={colorMetric}
            showArcs={showArcs} showBubbles={showBubbles} showHeat={showHeat} />
        )}
      </div>

      <div className="grid lg:grid-cols-3 gap-6">
        <ChartCard title="📍 Destination Detail"
        method={{
          formula: "Per destination node, aggregated over the filtered trips. Placed by exact city match, falling back to a pin-prefix centroid.",
          plot: "table",
        }} icon={MapIcon} iconColor="text-blue-400" className="lg:col-span-2"
          explain="Every mapped destination with volume and service metrics.">
          <div className="max-h-[380px] overflow-y-auto">
            <DataTable columns={[
              { key: 'destination', label: 'Destination', render: (r: GeoPoint) => <span className="text-gray-200">{r.destination}</span> },
              { key: 'trips', label: 'Trips' },
              {
                key: 'otd_pct', label: 'OTD %',
                render: (r: GeoPoint) => r.otd_pct != null
                  ? <span className={r.otd_pct >= 95 ? 'text-emerald-400' : r.otd_pct >= 80 ? 'text-amber-400' : 'text-red-400'}>{r.otd_pct}%</span> : '—',
              },
              { key: 'avg_transit_hours', label: 'Transit (h)' },
              { key: 'avg_distance_km', label: 'Avg km' },
              { key: 'total_km', label: 'Total km' },
            ]} data={points} />
          </div>
        </ChartCard>
        <ChartCard title="❓ Not Geolocated"
        method={{
          formula: "Destination names whose PIN did not resolve to a state and whose name is not in the offline city dictionary.",
          plot: "table",
          caveat: "These trips are missing from every bubble on this page, so the map understates by exactly this much.",
        }} icon={HelpCircle} iconColor="text-amber-400"
          explain="Destinations the offline geocoder couldn't place — add them to the city dictionary to close the gap.">
          <div className="max-h-[380px] overflow-y-auto">
            <DataTable columns={[
              { key: 'destination', label: 'Destination' },
              { key: 'trips', label: 'Trips' },
            ]} data={geo?.unmapped ?? []} emptyMessage="Everything is mapped 🎉" />
          </div>
        </ChartCard>
      </div>
    </PageContainer>
  );
}
