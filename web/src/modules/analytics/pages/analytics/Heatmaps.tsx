import { useMemo, useState } from 'react';
import { Clock, Truck, MapPin, Sigma, Sunrise, TrendingDown, TrendingUp } from 'lucide-react';
import PageContainer from '../../components/layout/PageContainer';
import Spinner from '../../components/ui/Spinner';
import ChartCard from '../../components/ui/ChartCard';
import MatrixHeatmap from '../../components/charts/MatrixHeatmap';
import BarChart from '../../components/charts/BarChart';
import { useApi } from '../../hooks/useApi';
import { useTTAFilters } from '../../components/tta/dashboard/FilterContext';
import { getDashCorrelation, getDashDowHour, getDashPivot } from '../../services/ttaDashboard';
import type { ColorScheme } from '../../lib/colorScales';
import { tc } from '../../../../core/theme';

const PIVOT_METRICS = [
  { key: 'otd_pct', label: 'OTD %', scheme: 'rdylgn' as ColorScheme, higherIsBetter: true, unit: '%' },
  { key: 'trips', label: 'Trips', scheme: 'viridis' as ColorScheme, higherIsBetter: true, unit: '' },
  { key: 'transit_hours', label: 'Transit hours', scheme: 'rdylgn_r' as ColorScheme, higherIsBetter: false, unit: ' h' },
  { key: 'detention_hours', label: 'Detention hours', scheme: 'rdylgn_r' as ColorScheme, higherIsBetter: false, unit: ' h' },
  { key: 'gps_uptime', label: 'GPS uptime', scheme: 'rdylgn' as ColorScheme, higherIsBetter: true, unit: '%' },
];

const METRIC_LABELS: Record<string, string> = {
  transit_hours: 'Transit h', planned_transit_hours: 'Planned h', detention_hours: 'Detention h',
  run_hours: 'Run h', stop_hours: 'Stop h', plant_vivo_hours: 'Plant vivo h',
  delivery_delta_hours: 'Delay h', dispatch_lead_hours: 'Lead h', distance_km: 'Distance km',
  avg_speed_kmph: 'Speed', speed_violations: 'Violations', gps_uptime: 'GPS %',
};

const DOW_FULL = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'];

/** One derived sentence, rendered as a card. */
function Insight({ label, value, note, tone = 'text-gray-200' }: {
  label: string; value: string; note?: string; tone?: string;
}) {
  return (
    <div className="bg-gray-950/60 border border-gray-800 rounded-lg px-3 py-2">
      <p className="text-xs text-gray-500">{label}</p>
      <p className={`text-sm font-bold ${tone}`}>{value}</p>
      {note && <p className="text-xs text-gray-600 leading-snug mt-0.5">{note}</p>}
    </div>
  );
}

export default function Heatmaps() {
  const { params, paramsKey } = useTTAFilters();
  const [metric, setMetric] = useState('otd_pct');

  const { data: dowHour, loading: l1 } = useApi(() => getDashDowHour(params), [paramsKey]);
  const { data: transMonth } = useApi(() => getDashPivot('transporter', metric, 12, params), [paramsKey, metric]);
  const { data: destMonth } = useApi(() => getDashPivot('destination', 'trips', 15, params), [paramsKey]);
  const { data: corr } = useApi(() => getDashCorrelation(params), [paramsKey]);

  const chosen = PIVOT_METRICS.find(m => m.key === metric)!;

  /**
   * The rhythm grid read for the reader.
   *
   * The grid itself only shows that some cells are darker. The four findings
   * below are what a planner would otherwise have to squint the numbers out
   * of: when the gate is busiest, how concentrated that is, how much dispatch
   * happens overnight, and which hours are empty capacity.
   */
  const rhythm = useMemo(() => {
    if (!dowHour?.values?.length) return null;
    const cells: { dow: number; hour: number; n: number }[] = [];
    dowHour.values.forEach((row, d) =>
      row.forEach((n, h) => cells.push({ dow: d, hour: h, n: n ?? 0 })));
    const total = cells.reduce((a, c) => a + c.n, 0);
    if (!total) return null;

    const byHour = Array.from({ length: 24 }, (_, h) => ({
      hour: `${String(h).padStart(2, '0')}:00`,
      trips: cells.filter(c => c.hour === h).reduce((a, c) => a + c.n, 0),
    }));
    const byDow = dowHour.values.map((row, d) => ({
      day: DOW_FULL[d], trips: row.reduce((a: number, n) => a + (n ?? 0), 0),
    }));

    const top = [...cells].sort((a, b) => b.n - a.n)[0];
    const top3 = [...cells].sort((a, b) => b.n - a.n).slice(0, 3)
      .reduce((a, c) => a + c.n, 0);
    const night = cells.filter(c => c.hour >= 22 || c.hour < 6)
      .reduce((a, c) => a + c.n, 0);
    const weekend = byDow[5].trips + byDow[6].trips;
    const busiestHour = [...byHour].sort((a, b) => b.trips - a.trips)[0];
    const emptyHours = byHour.filter(h => h.trips === 0).length;
    const busiestDay = [...byDow].sort((a, b) => b.trips - a.trips)[0];

    return {
      total, byHour, byDow, busiestHour, busiestDay, emptyHours,
      topSlot: `${DOW_FULL[top.dow]} ${String(top.hour).padStart(2, '0')}:00`,
      topSlotTrips: top.n,
      top3Pct: Math.round((100 * top3) / total),
      nightPct: Math.round((100 * night) / total),
      weekendPct: Math.round((100 * weekend) / total),
    };
  }, [dowHour]);

  /**
   * First period vs last, per row — the movement the eye cannot pick out of a
   * grid of numbers. Only rows with both endpoints present can move, so rows
   * with a gap are excluded rather than treated as a change from zero.
   */
  const movers = useMemo(() => {
    if (!transMonth?.rows?.length || transMonth.cols.length < 2) return [];
    return transMonth.rows.map((name, i) => {
      const series = transMonth.values[i] ?? [];
      const present = series.map((v, j) => ({ v, j })).filter(x => x.v != null);
      if (present.length < 2) return null;
      const first = present[0], last = present[present.length - 1];
      return {
        name,
        from: first.v as number,
        to: last.v as number,
        fromCol: transMonth.cols[first.j],
        toCol: transMonth.cols[last.j],
        delta: (last.v as number) - (first.v as number),
      };
    }).filter(Boolean) as {
      name: string; from: number; to: number; fromCol: string; toCol: string; delta: number;
    }[];
  }, [transMonth]);

  // Only rows that actually moved. A carrier flat at 100% is not "improving
  // most", and leaving it in puts the same name at the top of both lists.
  const moved = useMemo(() => movers.filter(m => Math.abs(m.delta) > 0.05), [movers]);
  const improving = useMemo(
    () => [...moved]
      .filter(m => (chosen.higherIsBetter ? m.delta > 0 : m.delta < 0))
      .sort((a, b) => (chosen.higherIsBetter ? b.delta - a.delta : a.delta - b.delta))
      .slice(0, 4),
    [moved, chosen]);
  const declining = useMemo(
    () => [...moved]
      .filter(m => (chosen.higherIsBetter ? m.delta < 0 : m.delta > 0))
      .sort((a, b) => (chosen.higherIsBetter ? a.delta - b.delta : b.delta - a.delta))
      .slice(0, 4),
    [moved, chosen]);

  /** The correlation pairs actually worth reading, strongest first. */
  const topPairs = useMemo(() => {
    if (!corr?.labels?.length) return [];
    const out: { a: string; b: string; r: number }[] = [];
    corr.labels.forEach((a, i) =>
      corr.labels.forEach((b, j) => {
        if (j <= i) return;
        const r = corr.values[i]?.[j];
        if (r != null && Math.abs(r) >= 0.3) out.push({ a, b, r });
      }));
    return out.sort((x, y) => Math.abs(y.r) - Math.abs(x.r)).slice(0, 6);
  }, [corr]);

  return (
    <PageContainer title="🔥 Heatmaps & Correlations">
      {/* --------------------------- departure rhythm ------------------------ */}
      <ChartCard title="🕐 Departure Rhythm — when trucks actually leave the plant"
        method={{
          formula: "A 7 × 24 cross-tabulation: each cell counts trips whose departure timestamp fell on that weekday and hour.",
          plot: "heatmap",
          caveat: "It is a picture of your own gate, not of the road — it says nothing about whether those trips arrived on time.",
        }}
        icon={Clock} iconColor="text-blue-400"
        explain="Each row is a weekday, each column is an hour of the clock, and each cell counts the trips that DEPARTED in that slot. Dark = many departures. It is a picture of your own gate, not of the road: it says nothing about whether those trips arrived on time.">
        {l1 ? <Spinner /> : dowHour && (
          <>
            <div className="overflow-x-auto">
              <MatrixHeatmap rows={dowHour.rows} cols={dowHour.cols} values={dowHour.values}
                scheme="blues" cellW={32} cellH={26} labelW={60}
                valueFormatter={(v) => (v === 0 ? '' : v.toFixed(0))} />
            </div>

            {rhythm && (
              <>
                <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3 mt-5">
                  <Insight label="Busiest single slot" value={rhythm.topSlot}
                    note={`${rhythm.topSlotTrips} departures`} />
                  <Insight label="Busiest hour, any day" value={rhythm.busiestHour.hour}
                    note={`${rhythm.busiestHour.trips} departures`} />
                  <Insight label="Busiest day" value={rhythm.busiestDay.day}
                    note={`${rhythm.busiestDay.trips} departures`} />
                  <Insight label="Top 3 slots hold" value={`${rhythm.top3Pct}%`}
                    note="of all departures — the gate rush"
                    tone={rhythm.top3Pct > 30 ? 'text-amber-400' : 'text-gray-200'} />
                  <Insight label="Dispatched overnight" value={`${rhythm.nightPct}%`}
                    note="22:00–06:00" />
                  <Insight label="Hours never used" value={`${rhythm.emptyHours} / 24`}
                    note="idle gate capacity" />
                </div>

                <div className="bg-gray-950/50 border border-gray-800 rounded-lg p-4 mt-4">
                  <p className="text-xs font-semibold text-gray-300 mb-2 flex items-center gap-1.5">
                    <Sunrise className="w-3.5 h-3.5 text-amber-400" /> How to read it
                  </p>
                  <ul className="text-xs text-gray-400 space-y-1.5 leading-relaxed list-disc pl-4">
                    <li>
                      <span className="text-gray-300">A dark vertical band</span> is a gate rush:
                      every truck arrives for the same slot, so they queue against each other and
                      that queue is billed back to you as detention. Moving even a tenth of those
                      loads into the pale columns either side costs nothing and shortens the queue
                      for everyone.
                    </li>
                    <li>
                      <span className="text-gray-300">A pale column</span> is loading capacity you
                      already pay for and do not use. Compare it against the detention figures on
                      the Overview — if detention peaks in the dark band, the two charts are
                      describing the same problem from opposite sides.
                    </li>
                    <li>
                      <span className="text-gray-300">A dark row</span> means one weekday carries
                      disproportionate dispatch. That concentration is what turns a single bad day
                      — a strike, a breakdown, a holiday — into a missed week.
                    </li>
                    <li>
                      <span className="text-gray-300">Late-evening departures</span> put the
                      long-haul leg into the small hours, which is where fatigue incidents
                      cluster. The Speed &amp; Safety page measures how much driving actually
                      happens then; this grid only shows when they set off.
                    </li>
                  </ul>
                </div>

                <div className="grid lg:grid-cols-2 gap-6 mt-5">
                  <div>
                    <p className="text-xs text-gray-400 mb-2">Departures by hour (all days)</p>
                    <BarChart data={rhythm.byHour} xKey="hour" height={220} angledLabels
                      series={[{ key: 'trips', label: 'Departures', color: tc('#3b82f6') }]} />
                  </div>
                  <div>
                    <p className="text-xs text-gray-400 mb-2">Departures by weekday</p>
                    <BarChart data={rhythm.byDow} xKey="day" height={220} angledLabels
                      series={[{ key: 'trips', label: 'Departures', color: tc('#06b6d4') }]} />
                  </div>
                </div>
                <p className="text-xs text-gray-500 mt-2">
                  The two bars are the grid's own row and column totals — the same data with one
                  dimension collapsed, which is usually the easier read when you only need to know
                  "when", not "when on which day".
                </p>
              </>
            )}
          </>
        )}
      </ChartCard>

      {/* ------------------------ transporter x month ------------------------ */}
      <ChartCard title="🚚 Top Transporters × Month"
        method={{
          formula: "Top 12 carriers by volume against calendar month, cell = the selected metric (mean for averages, count for trips).",
          plot: "heatmap",
          caveat: "Read along a row, not down a column. A blank cell means the carrier ran no trips that month, which is itself worth a question.",
        }} icon={Truck} iconColor="text-purple-400" className="mt-6"
        explain={`One row per carrier, one column per month, coloured by ${chosen.label.toLowerCase()}. Read along a row, not down a column: a row that darkens month after month is a carrier sliding, and catching that is worth more than any single month's ranking. Colour is scaled across the whole grid, so two cells of the same shade mean the same value.`}
        actions={
          <select value={metric} onChange={e => setMetric(e.target.value)}
            className="bg-gray-800 border border-gray-700 rounded-lg px-2 py-1.5 text-xs text-gray-300 outline-none">
            {PIVOT_METRICS.map(m => <option key={m.key} value={m.key}>{m.label}</option>)}
          </select>
        }>
        {transMonth ? (
          <>
            <div className="overflow-x-auto">
              <MatrixHeatmap rows={transMonth.rows} cols={transMonth.cols} values={transMonth.values}
                scheme={chosen.scheme} cellW={64} cellH={30} labelW={210} />
            </div>
            {!!moved.length && (
              <div className="grid md:grid-cols-2 gap-4 mt-5">
                <div>
                  <p className="text-xs font-semibold text-emerald-400 mb-2 flex items-center gap-1.5">
                    <TrendingUp className="w-3.5 h-3.5" /> Improving most
                  </p>
                  {improving.length ? improving.map(m => (
                    <div key={m.name} className="flex items-center justify-between gap-3 text-xs py-1 border-b border-gray-800/60">
                      <span className="text-gray-300 truncate">{m.name}</span>
                      <span className="text-emerald-400 shrink-0">
                        {m.from}{chosen.unit} → {m.to}{chosen.unit}
                        <span className="text-gray-600"> ({m.fromCol}→{m.toCol})</span>
                      </span>
                    </div>
                  )) : <p className="text-xs text-gray-500">No carrier improved on this metric.</p>}
                </div>
                <div>
                  <p className="text-xs font-semibold text-red-400 mb-2 flex items-center gap-1.5">
                    <TrendingDown className="w-3.5 h-3.5" /> Sliding most
                  </p>
                  {declining.length ? declining.map(m => (
                    <div key={m.name} className="flex items-center justify-between gap-3 text-xs py-1 border-b border-gray-800/60">
                      <span className="text-gray-300 truncate">{m.name}</span>
                      <span className="text-red-400 shrink-0">
                        {m.from}{chosen.unit} → {m.to}{chosen.unit}
                        <span className="text-gray-600"> ({m.fromCol}→{m.toCol})</span>
                      </span>
                    </div>
                  )) : <p className="text-xs text-gray-500">No carrier slid on this metric.</p>}
                </div>
              </div>
            )}
            <p className="text-xs text-gray-500 mt-3">
              First recorded month against last, per carrier. Carriers appearing in only one
              month are left out rather than shown as a change from nothing, and so are those
              that did not move. A blank cell means that carrier ran no trips that month,
              which is itself worth a question.
            </p>
          </>
        ) : <Spinner />}
      </ChartCard>

      {/* ------------------------ destination x month ------------------------ */}
      <ChartCard title="📍 Top Destinations × Month (Trip Volume)"
        method={{
          formula: "Top 15 destinations by volume against month; each cell is the trip count.",
          plot: "heatmap",
        }} icon={MapPin} iconColor="text-cyan-400" className="mt-6"
        explain="Where demand is moving. A dark cell appearing in a row that used to be pale is a region ramping up — pre-position carrier capacity there before the service slips, rather than after.">
        {destMonth ? (
          <div className="overflow-x-auto">
            <MatrixHeatmap rows={destMonth.rows} cols={destMonth.cols} values={destMonth.values}
              scheme="blues" cellW={64} cellH={30} labelW={210}
              valueFormatter={(v) => v.toFixed(0)} />
          </div>
        ) : <Spinner />}
      </ChartCard>

      {/* ---------------------------- correlations --------------------------- */}
      <ChartCard title="🧮 Correlation Matrix"
        method={{
          formula: "Pairwise Pearson correlation across the numeric trip metrics, computed over trips where both metrics are present.",
          plot: "matrix",
          caveat: "Correlation is not causation. It narrows where to look; two metrics can move together because a third drives both.",
        }} icon={Sigma} iconColor="text-red-400" className="mt-6"
        explain="How the trip metrics move together, from −1 (opposite) through 0 (unrelated) to +1 (in lockstep). Its use is to tell you which lever to pull: if stop hours track delay but speed does not, the fix is fewer stops, not faster driving. Correlation is not causation — it narrows where to look, it does not prove why.">
        {corr && corr.labels.length >= 2 ? (
          <>
            <div className="overflow-x-auto">
              <MatrixHeatmap
                rows={corr.labels.map(l => METRIC_LABELS[l] ?? l)}
                cols={corr.labels.map(l => METRIC_LABELS[l] ?? l)}
                values={corr.values} scheme="rdbu" diverging
                cellW={64} cellH={30} labelW={110}
                valueFormatter={(v) => v.toFixed(2)} />
            </div>
            {!!topPairs.length && (
              <div className="mt-5">
                <p className="text-xs font-semibold text-gray-300 mb-2">Strongest relationships</p>
                <div className="grid md:grid-cols-2 gap-2">
                  {topPairs.map(p => (
                    <div key={`${p.a}-${p.b}`}
                      className="flex items-center justify-between gap-3 bg-gray-950/60 border border-gray-800 rounded-lg px-3 py-1.5 text-xs">
                      <span className="text-gray-300">
                        {METRIC_LABELS[p.a] ?? p.a} ↔ {METRIC_LABELS[p.b] ?? p.b}
                      </span>
                      <span className={p.r > 0 ? 'text-blue-400' : 'text-red-400'}>
                        {p.r > 0 ? '+' : ''}{p.r.toFixed(2)} · {p.r > 0 ? 'rise together' : 'move opposite'}
                      </span>
                    </div>
                  ))}
                </div>
                <p className="text-xs text-gray-500 mt-2">
                  Only pairs above |0.3| are listed; anything weaker is noise on this many trips.
                  The diagonal is always 1.00 — a metric correlates perfectly with itself — so
                  read the off-diagonal cells only.
                </p>
              </div>
            )}
          </>
        ) : <p className="text-gray-500 text-sm py-6">Needs more than 10 trips with populated metrics to compute correlations.</p>}
      </ChartCard>
    </PageContainer>
  );
}
