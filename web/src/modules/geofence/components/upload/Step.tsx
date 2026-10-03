/**
 * One pipeline step, as the page shows it.
 *
 * The shape is the same for all twenty-five, because the claim being made is
 * the same each time: here is what this step does, here is why it has to exist,
 * here is the rule in symbols, here are the numbers it produced on *your* trip,
 * here is one case worked through by hand, and here is the whole table to
 * download and check.
 *
 * The downloads serve the same document this component renders, so what somebody
 * opens in Excel is what they were shown — not a re-query that might have moved.
 */
import { useState, type ReactNode } from 'react';
import { ChevronDown, ChevronRight, Download, FileJson, FileSpreadsheet, Table2 } from 'lucide-react';
import { api, uploadUrls } from '../../lib/api';
import { DataTable, Pagination, Badge } from '../ui';
import { fmtInt } from '../../lib/format';

export interface StepDoc {
  key: string;
  title: string;
  what: string;
  why: string;
  how: string;
  note?: string;
  stats: Record<string, any>;
  columns: string[];
  rows: any[][];
  total_rows?: number;
  truncated?: boolean;
  example?: any;
  extra?: Record<string, any>;
}

/** Numbers right-aligned and thousands-separated; text left. */
function cell(v: any): ReactNode {
  if (v === null || v === undefined || v === '') return <span className="text-gray-600">—</span>;
  if (typeof v === 'number') return <span className="tabular">{v.toLocaleString('en-IN', { maximumFractionDigits: 6 })}</span>;
  if (typeof v === 'boolean') return v ? 'yes' : 'no';
  if (Array.isArray(v)) return v.join(', ');
  return String(v);
}

const NUMERIC = /(\(m\)|\(s\)|\(h\)|\(km\)|\(m²\)|\(deg²\)|km\/h|%|^fixes$|^visits$|^nodes$|^count$)/i;

export function StatGrid({ stats }: { stats: Record<string, any> }) {
  const entries = Object.entries(stats).filter(([, v]) => v !== undefined);
  if (!entries.length) return null;
  return (
    <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 2xl:grid-cols-6 gap-2">
      {entries.map(([k, v]) => (
        <div key={k} className="bg-gray-800/50 rounded-lg px-3 py-2 min-w-0">
          <p className="text-xs text-gray-500 leading-tight" title={k}>{k}</p>
          <p className="text-sm text-gray-100 font-medium tabular mt-0.5 break-words">
            {typeof v === 'number' ? v.toLocaleString('en-IN', { maximumFractionDigits: 6 })
              : v === null ? '—' : typeof v === 'boolean' ? (v ? 'yes' : 'no')
              : Array.isArray(v) ? v.join(' × ') : String(v)}
          </p>
        </div>
      ))}
    </div>
  );
}

/** The paragraphs. `**bold**` is honoured because the prose uses it to name
 *  the two or three ideas a step turns on. */
function Prose({ text }: { text: string }) {
  if (!text) return null;
  return (
    <>
      {text.split('\n\n').map((para, i) => (
        <p key={i} className="text-xs text-gray-400 leading-relaxed mb-2 last:mb-0">
          {para.split(/(\*\*[^*]+\*\*)/g).map((piece, j) =>
            piece.startsWith('**') && piece.endsWith('**')
              ? <strong key={j} className="text-gray-200 font-medium">{piece.slice(2, -2)}</strong>
              : <span key={j}>{piece}</span>)}
        </p>
      ))}
    </>
  );
}

export function Worked({ example }: { example: any }) {
  if (!example?.steps?.length) return null;
  const chips = Object.entries(example).filter(
    ([k, v]) => k !== 'steps' && k !== 'edges' && k !== 'window'
      && (typeof v === 'string' || typeof v === 'number'));
  return (
    <div className="rounded-lg border border-blue-900/40 bg-blue-950/10 p-4">
      <p className="text-xs font-medium text-blue-300 mb-2.5">Worked through, on one case from this trip</p>
      {chips.length > 0 && (
        <div className="flex flex-wrap gap-1.5 mb-3">
          {chips.map(([k, v]) => (
            <span key={k} className="text-xs px-2 py-0.5 rounded bg-gray-800 text-gray-400">
              {k.replace(/_/g, ' ')}: <span className="text-gray-200">{String(v)}</span>
            </span>
          ))}
        </div>
      )}
      <ol className="space-y-2">
        {example.steps.map((s: string, i: number) => (
          <li key={i} className="flex gap-2.5 text-xs text-gray-300 leading-relaxed">
            <span className="shrink-0 w-5 h-5 rounded-full bg-blue-600/20 text-blue-300 text-xs font-semibold flex items-center justify-center mt-0.5">
              {i + 1}
            </span>
            <span>{s}</span>
          </li>
        ))}
      </ol>
      {example.edges && (
        <div className="mt-3">
          <p className="text-xs text-gray-500 mb-1.5">Distance from the fix to each edge of the ring, nearest first</p>
          <DataTable dense rows={example.edges.map((e: any[]) => ({
            edge: e[0], from: `${e[1]}, ${e[2]}`, to: `${e[3]}, ${e[4]}`, t: e[5], d: e[6],
          }))} columns={[
            { key: 'edge', label: 'Edge' },
            { key: 'from', label: 'From vertex' },
            { key: 'to', label: 'To vertex' },
            { key: 't', label: 'Nearest point along it (0–1)', align: 'right' },
            { key: 'd', label: 'Distance (m)', align: 'right', render: (r: any) => r.d?.toLocaleString('en-IN') },
          ]} />
        </div>
      )}
      {example.window && (
        <div className="mt-3">
          <p className="text-xs text-gray-500 mb-1.5">The window the median was taken over</p>
          <DataTable dense rows={example.window.map((w: any[]) => ({ ts: w[0], lat: w[1], lon: w[2] }))}
            columns={[{ key: 'ts', label: 'Timestamp' }, { key: 'lat', label: 'Raw lat', align: 'right' },
              { key: 'lon', label: 'Raw lon', align: 'right' }]} />
        </div>
      )}
    </div>
  );
}

export default function Step({ doc, uploadId, index, children, defaultOpen = false }: {
  doc: StepDoc; uploadId: number | string; index: number; children?: ReactNode;
  defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const [page, setPage] = useState(1);
  const [rows, setRows] = useState<any[][]>(doc.rows || []);
  const [loading, setLoading] = useState(false);
  const total = doc.total_rows ?? doc.rows?.length ?? 0;
  const pageSize = 200;
  const pages = Math.max(1, Math.ceil(total / pageSize));

  const goto = async (p: number) => {
    setPage(p);
    if (p === 1 && doc.rows?.length) { setRows(doc.rows); return; }
    setLoading(true);
    try {
      const d = await api.uploadStep(uploadId, doc.key, p, pageSize);
      setRows(d.rows);
    } finally { setLoading(false); }
  };

  const table = doc.columns?.length > 0;
  const cols = (doc.columns || []).map((c, i) => ({
    key: String(i),
    label: c,
    align: (NUMERIC.test(c) ? 'right' : 'left') as 'right' | 'left',
    render: (r: any) => cell(r[i]),
  }));

  return (
    <section id={`step-${doc.key}`} className="bg-gray-900 rounded-xl border border-gray-800 scroll-mt-4">
      <button onClick={() => setOpen(o => !o)}
        className="w-full flex items-start gap-3 p-5 text-left hover:bg-gray-800/30 rounded-xl transition-colors">
        <span className="shrink-0 w-7 h-7 rounded-lg bg-blue-600/15 text-blue-400 text-xs font-bold flex items-center justify-center mt-0.5">
          {index}
        </span>
        <span className="min-w-0 flex-1">
          <span className="flex items-center gap-2 flex-wrap">
            <h2 className="text-base font-semibold text-white">{doc.title.replace(/^Step \d+ · /, '')}</h2>
            {total > 0 && <Badge>{fmtInt(total)} rows</Badge>}
          </span>
          <span className="block text-xs text-gray-400 mt-1 leading-relaxed line-clamp-2">{doc.what}</span>
        </span>
        {open ? <ChevronDown className="w-5 h-5 text-gray-500 shrink-0 mt-1" />
          : <ChevronRight className="w-5 h-5 text-gray-500 shrink-0 mt-1" />}
      </button>

      {open && (
        <div className="px-5 pb-5 space-y-4">
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            <div>
              <p className="text-xs font-semibold uppercase tracking-wider text-gray-500 mb-1.5">Why it exists</p>
              <Prose text={doc.why} />
            </div>
            <div>
              <p className="text-xs font-semibold uppercase tracking-wider text-gray-500 mb-1.5">The rule</p>
              <pre className="text-xs text-cyan-300/90 bg-gray-950/60 border border-gray-800 rounded-lg p-3 whitespace-pre-wrap font-mono leading-relaxed">
                {doc.how || '—'}
              </pre>
              {doc.note && <p className="text-xs text-amber-300/80 mt-2 leading-relaxed">{doc.note}</p>}
            </div>
          </div>

          <div>
            <p className="text-xs font-semibold uppercase tracking-wider text-gray-500 mb-1.5">
              What it did on this trip
            </p>
            <StatGrid stats={doc.stats} />
          </div>

          {doc.example && <Worked example={doc.example} />}
          {children}

          {table && (
            <div>
              <div className="flex items-center justify-between gap-3 flex-wrap mb-2">
                <p className="text-xs font-semibold uppercase tracking-wider text-gray-500 flex items-center gap-1.5">
                  <Table2 className="w-3.5 h-3.5" /> Every row, {fmtInt(total)} in all
                </p>
                <div className="flex items-center gap-1.5">
                  <a href={uploadUrls.step(uploadId, doc.key, 'csv')}
                    className="flex items-center gap-1 px-2 py-1 rounded-md text-xs bg-gray-800 text-gray-300 hover:bg-gray-700">
                    <Download className="w-3 h-3" /> CSV
                  </a>
                  <a href={uploadUrls.step(uploadId, doc.key, 'xlsx')}
                    className="flex items-center gap-1 px-2 py-1 rounded-md text-xs bg-gray-800 text-gray-300 hover:bg-gray-700">
                    <FileSpreadsheet className="w-3 h-3" /> Excel
                  </a>
                  <a href={uploadUrls.step(uploadId, doc.key, 'json')}
                    className="flex items-center gap-1 px-2 py-1 rounded-md text-xs bg-gray-800 text-gray-300 hover:bg-gray-700">
                    <FileJson className="w-3 h-3" /> JSON
                  </a>
                </div>
              </div>
              <div className="max-h-[520px] overflow-y-auto rounded-lg border border-gray-800">
                <DataTable dense loading={loading} columns={cols} rows={rows}
                  empty="This step had nothing to report on this trip — which is itself the result." />
              </div>
              <Pagination page={page} pages={pages} total={total} onPage={goto} />
            </div>
          )}
        </div>
      )}
    </section>
  );
}
