import { useMemo, useState } from 'react';
import {
  ArrowRight, Calculator, Database, Sigma, AlertTriangle, Search,
  FlaskConical, Ban, Info,
} from 'lucide-react';
import PageContainer from '../components/layout/PageContainer';
import ChartCard from '../components/ui/ChartCard';
import Spinner from '../components/ui/Spinner';
import { useApi } from '../hooks/useApi';
import { useTTAFilters } from '../components/tta/dashboard/FilterContext';
import { getManual, type Derivation, type FeedField, type CoverageRow } from '../services/manual';
import { formatNumber } from '../lib/formatters';

/**
 * The calculation manual.
 *
 * Every figure on the platform, traced from the JSON the upstream API sends to
 * the number on screen. Built because "where does this come from?" was being
 * answered from memory, and two of those answers were wrong in ways that
 * changed decisions — detention described as plant time when it measures the
 * delivery point, and a count of GPS samples read as a count of speeding
 * incidents.
 *
 * The worked example is the part that earns the page. A formula in a document
 * can be out of date; a formula shown beside the value it produced for a real
 * trip, pulled from the same frame the dashboards read, cannot be.
 */
export default function Manual() {
  const { params, paramsKey } = useTTAFilters();
  const { data, loading, error } = useApi(() => getManual(params), [paramsKey]);
  const [q, setQ] = useState('');

  const match = (s: string) => s.toLowerCase().includes(q.toLowerCase());

  const derivGroups = useMemo(() => {
    const rows = (data?.derivations ?? []).filter(
      d => !q || match(d.label) || match(d.key) || match(d.formula) || match(d.why));
    const by = new Map<string, Derivation[]>();
    rows.forEach(d => by.set(d.group, [...(by.get(d.group) ?? []), d]));
    return [...by.entries()];
  }, [data, q]);

  const feedRows = (rows: FeedField[]) =>
    rows.filter(r => !q || match(r.column) || match(r.note) ||
      r.json_keys.some(k => match(k)));

  if (loading) return <PageContainer title="Calculation Manual"><Spinner /></PageContainer>;
  if (error || !data) {
    return (
      <PageContainer title="Calculation Manual">
        <p className="text-gray-500">{error || 'Could not load the manual.'}</p>
      </PageContainer>
    );
  }

  const ex = data.example;

  return (
    <PageContainer title="Calculation Manual">
      <p className="text-sm text-gray-400 max-w-3xl mb-4">
        Every number on this platform, traced from the JSON the upstream API sends through to
        the figure on screen. The worked example below runs a real trip through each formula,
        so the arithmetic can be checked rather than taken on trust.
      </p>

      <div className="relative mb-6 max-w-md">
        <Search className="w-4 h-4 text-gray-500 absolute left-3 top-1/2 -translate-y-1/2" />
        <input value={q} onChange={e => setQ(e.target.value)}
          placeholder="Find a field or a formula…"
          className="w-full pl-10 pr-3 py-2 bg-gray-800 border border-gray-700 rounded-lg text-sm text-gray-200 focus:outline-none focus:border-blue-500" />
      </div>

      {/* ---------------------------------------------------------------- */}
      <Section title="1 · The feed" icon={Database}
        blurb="What the upstream API actually sends, and the column each field lands in.
               The two report lanes name the same thing differently; the ingest mapper takes
               the first key present, so the keys are listed in the order it tries them." />

      <div className="grid xl:grid-cols-2 gap-6">
        <ChartCard title="Trip record → tta_trips" icon={Database} iconColor="text-blue-400"
          explain="One row per trip. These are the identity and timeline fields every other calculation hangs off.">
          <FeedTable rows={feedRows(data.feed.trip_fields)} />
        </ChartCard>
        <ChartCard title="Trip record → tta_trip_metrics" icon={Database} iconColor="text-cyan-400"
          explain="The provider's own measurements for the same trip, kept in a second table because the feed can send a trip before it sends its metrics.">
          <FeedTable rows={feedRows(data.feed.metric_fields)} />
        </ChartCard>
      </div>

      <ChartCard title="How the text values become numbers" icon={Calculator}
        iconColor="text-purple-400" className="mt-6"
        explain="The feed sends durations as prose, not as minutes. These are the exact conversions, and they are covered by tests so the examples cannot go stale.">
        <div className="space-y-3">
          {data.feed.parsers.map(p => (
            <div key={p.name} className="border-l-2 border-gray-800 pl-3">
              <p className="text-xs font-semibold text-gray-300 uppercase tracking-wide">{p.name}</p>
              <p className="text-sm text-emerald-300 font-mono mt-1 break-words">{p.examples}</p>
              {p.note && <p className="text-xs text-gray-500 mt-1">{p.note}</p>}
            </div>
          ))}
        </div>
      </ChartCard>

      {/* ---------------------------------------------------------------- */}
      <Section title="2 · The derivations" icon={Sigma}
        blurb="Every computed column, with the formula, the inputs it reads, and the rule that
               decides when it is NULL rather than zero. The NULL rule is the part that gets
               misread: unmeasured is not the same as zero, and treating it as zero is what
               produced a 100%-on-time carrier out of nineteen measured trips." />

      {derivGroups.map(([group, rows]) => (
        <ChartCard key={group} title={group} icon={Sigma} iconColor="text-amber-400"
          className="mb-6">
          <div className="space-y-4">
            {rows.map(d => <DerivationRow key={d.key} d={d} />)}
          </div>
        </ChartCard>
      ))}
      {!derivGroups.length && (
        <p className="text-sm text-gray-500 py-6">No derivation matches “{q}”.</p>
      )}

      {/* ---------------------------------------------------------------- */}
      <Section title="3 · A worked example" icon={FlaskConical}
        blurb="One real trip from the current window, carried through every formula above with
               its own numbers." />

      <ChartCard
        title={ex.available ? `Trip ${ex.trip_id}` : 'No example available'}
        icon={FlaskConical} iconColor="text-emerald-400"
        explain={ex.available
          ? `${ex.transporter} on ${ex.lane} · ${ex.trip_class} lane. Every value below is computed by the same code that produces the dashboards — this is not a transcription.`
          : ex.reason}>
        {!ex.available ? (
          <p className="text-sm text-gray-500 py-4">{ex.reason}</p>
        ) : (
          <>
            {ex.partial && (
              <div className="rounded-lg border border-amber-800/60 bg-amber-950/20 px-3 py-2 mb-4">
                <p className="text-xs text-amber-300">
                  No trip in this window carries every input, so this is the most complete one
                  available. The formulas still hold; some show no value because the feed sent
                  nothing for them.
                </p>
              </div>
            )}

            <p className="text-xs uppercase tracking-wide text-gray-500 mb-2">
              What the API sent for this trip
            </p>
            <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-x-6 gap-y-1.5 mb-5 pb-5 border-b border-gray-800">
              {Object.entries(ex.raw ?? {}).map(([k, v]) => (
                <div key={k} className="flex items-baseline justify-between gap-2 text-xs">
                  <span className="text-gray-500 font-mono truncate">{k}</span>
                  <span className={v == null ? 'text-gray-600 italic' : 'text-gray-200 font-medium'}>
                    {v == null ? 'not sent' : String(v)}
                  </span>
                </div>
              ))}
            </div>

            <p className="text-xs uppercase tracking-wide text-gray-500 mb-2">
              What the platform computes from it
            </p>
            <div className="space-y-2.5">
              {(ex.steps ?? []).map(s => (
                <div key={s.key} className="grid md:grid-cols-[1fr_auto] gap-2 items-baseline
                                            border-b border-gray-800/50 pb-2.5">
                  <div className="min-w-0">
                    <p className="text-sm text-gray-200">{s.label}</p>
                    <p className="text-xs text-gray-500 font-mono break-words">{s.formula}</p>
                    <p className="text-xs text-gray-600 mt-0.5">
                      {Object.entries(s.inputs).map(([k, v], i) => (
                        <span key={k}>
                          {i > 0 && <span className="text-gray-700"> · </span>}
                          <span className="font-mono">{k}</span>
                          {' = '}
                          <span className={v == null ? 'italic text-gray-700' : 'text-gray-400'}>
                            {v == null ? 'null' : String(v)}
                          </span>
                        </span>
                      ))}
                    </p>
                  </div>
                  <p className={`text-right font-semibold whitespace-nowrap ${
                    s.value == null ? 'text-gray-600 italic text-xs' : 'text-emerald-300'}`}>
                    {s.value == null ? 'not measured' : `${s.value} ${s.unit}`}
                  </p>
                </div>
              ))}
            </div>
          </>
        )}
      </ChartCard>

      {/* ---------------------------------------------------------------- */}
      <Section title="4 · What is actually measured" icon={AlertTriangle}
        blurb="Every percentage on the platform is computed over the trips that carry its input,
               not over all trips. This is that denominator. The lane split is the whole story:
               the two feeds report almost disjoint metric sets, so a fleet-wide coverage figure
               is really 'one lane reports this and the other never does'." />

      <ChartCard title="Input coverage" icon={AlertTriangle} iconColor="text-amber-400"
        explain={`Across ${formatNumber(data.coverage.total)} trips in the selected window. A low figure does not mean the metric is bad — it means most trips have no value for it at all, so any average is taken over the minority that do.`}>
        <CoverageTable rows={data.coverage.rows} lanes={data.coverage.lanes} />
      </ChartCard>

      {data.not_derivable?.length > 0 && (
        <ChartCard title="Asked for, and not derivable from this feed" icon={Ban}
          iconColor="text-red-400" className="mt-6"
          explain="Stated here rather than shipped as an empty chart. Each one says what is missing and what would make it possible.">
          <div className="space-y-4">
            {data.not_derivable.map(n => (
              <div key={n.name} className="border-l-2 border-red-900 pl-3">
                <p className="text-sm text-gray-200 font-medium">{n.name}</p>
                <p className="text-xs text-gray-400 mt-1 leading-relaxed">{n.reason}</p>
                <p className="text-xs text-emerald-400/80 mt-1.5 leading-relaxed">
                  <span className="text-gray-500">Needed: </span>{n.needed}
                </p>
              </div>
            ))}
          </div>
        </ChartCard>
      )}
    </PageContainer>
  );
}

// ----------------------------------------------------------------------

function Section({ title, icon: Icon, blurb }: {
  title: string; icon: any; blurb: string;
}) {
  return (
    <div className="mt-10 mb-4">
      <div className="flex items-center gap-3 mb-2">
        <Icon className="w-4 h-4 text-gray-500" />
        <h2 className="text-xs font-semibold uppercase tracking-wider text-gray-400">{title}</h2>
        <div className="flex-1 h-px bg-gray-800" />
      </div>
      <p className="text-xs text-gray-500 max-w-3xl leading-relaxed">{blurb}</p>
    </div>
  );
}

function FeedTable({ rows }: { rows: FeedField[] }) {
  if (!rows.length) return <p className="text-sm text-gray-500 py-4">No field matches.</p>;
  return (
    <div className="overflow-x-auto max-h-[460px] overflow-y-auto">
      <table className="w-full text-sm">
        <thead className="sticky top-0 bg-gray-900">
          <tr className="border-b border-gray-800">
            {['API key', '', 'Column', 'Notes'].map((h, i) => (
              <th key={i} className="px-2 py-2 text-left text-xs text-gray-500 uppercase tracking-wide">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map(r => (
            <tr key={r.column} className="border-b border-gray-800/50 align-top">
              <td className="px-2 py-2">
                {r.json_keys.map((k, i) => (
                  <span key={k} className={`block font-mono text-xs ${
                    i === 0 ? 'text-blue-300' : 'text-gray-600'}`}
                    title={i === 0 ? 'tried first' : 'fallback for the other lane'}>
                    {k}
                  </span>
                ))}
              </td>
              <td className="px-1 py-2 text-gray-700"><ArrowRight className="w-3 h-3" /></td>
              <td className="px-2 py-2">
                <span className="font-mono text-xs text-gray-200">{r.column}</span>
                <span className="block text-xs text-gray-600">{r.parser}</span>
              </td>
              <td className="px-2 py-2 text-xs text-gray-500 leading-relaxed min-w-[220px]">
                {r.note}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function DerivationRow({ d }: { d: Derivation }) {
  return (
    <div className="border-l-2 border-gray-800 pl-3">
      <div className="flex items-baseline gap-2 flex-wrap">
        <span className="text-sm text-gray-200 font-medium">{d.label}</span>
        <span className="font-mono text-xs text-gray-600">{d.key}</span>
        {d.unit && <span className="text-xs text-gray-600">({d.unit})</span>}
      </div>
      <p className="text-sm text-emerald-300 font-mono mt-1 break-words">{d.formula}</p>
      <p className="text-xs text-amber-400/80 mt-1">
        <span className="text-gray-500">NULL when: </span>{d.null_rule}
      </p>
      <p className="text-xs text-gray-500 mt-1.5 leading-relaxed">{d.why}</p>
    </div>
  );
}

function CoverageTable({ rows, lanes }: { rows: CoverageRow[]; lanes: string[] }) {
  if (!rows.length) return <p className="text-sm text-gray-500 py-4">No trips in this window.</p>;
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-gray-800">
            <th className="px-2 py-2 text-left text-xs text-gray-500 uppercase tracking-wide">Input</th>
            <th className="px-2 py-2 text-left text-xs text-gray-500 uppercase tracking-wide">Overall</th>
            {lanes.map(l => (
              <th key={l} className="px-2 py-2 text-left text-xs text-gray-500 uppercase tracking-wide">
                {l} lane
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map(r => (
            <tr key={r.column} className="border-b border-gray-800/50">
              <td className="px-2 py-2">
                <span className="text-gray-200">{r.label}</span>
                <span className="block font-mono text-xs text-gray-600">{r.column}</span>
              </td>
              <td className="px-2 py-2 min-w-[150px]">
                <Bar pct={r.pct} measured={r.measured} total={r.total} />
              </td>
              {lanes.map(l => {
                const v = r.by_lane[l];
                return (
                  <td key={l} className="px-2 py-2 min-w-[150px]">
                    {v ? <Bar pct={v.pct} measured={v.measured} total={v.total} />
                      : <span className="text-gray-700 text-xs">—</span>}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
      <p className="text-xs text-gray-600 mt-3 flex items-start gap-1.5">
        <Info className="w-3 h-3 mt-0.5 shrink-0" />
        A 0% lane is not a data-quality problem to chase — that feed does not carry the field at
        all. It is the reason a figure can be measured on a quarter of the book and still be the
        best number available.
      </p>
    </div>
  );
}

function Bar({ pct, measured, total }: { pct: number; measured: number; total: number }) {
  // Colour on how much of the book the figure rests on: under half and any
  // average taken over it describes a minority of trips.
  const tone = pct >= 90 ? 'bg-emerald-500' : pct >= 50 ? 'bg-amber-500' : 'bg-red-500';
  return (
    <div title={`${formatNumber(measured)} of ${formatNumber(total)} trips`}>
      <div className="flex items-baseline justify-between text-xs mb-1">
        <span className={pct >= 50 ? 'text-gray-300' : 'text-red-400'}>{pct}%</span>
        <span className="text-xs text-gray-600">{formatNumber(measured)}</span>
      </div>
      <div className="w-full h-1.5 bg-gray-800 rounded-full overflow-hidden">
        <div className={`h-full rounded-full ${tone}`} style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}
