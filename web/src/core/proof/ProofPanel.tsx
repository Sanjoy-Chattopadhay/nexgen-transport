import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { ArrowDown, ArrowUp, CheckCircle2, ChevronLeft, ChevronRight, Download, FlaskConical, Loader2, TriangleAlert, X } from 'lucide-react';
import { backendApi } from '../../modules/analytics/services/api';
import { ProofContext, type OpenProof } from './context';

/**
 * How a figure was calculated, and the records that prove it.
 *
 * Opened from a tile (KPICard with `proof`), drawn full width beneath the row
 * of tiles by ProofGrid. The server recounts the figure from the records it
 * returns (nexgen/services/analytics/proof.py); the panel puts that recount
 * beside the number on the tile, so agreement is visible rather than claimed.
 */

interface Column { key: string; label: string; kind?: 'datetime' | 'number' | 'bool'; digits?: number; link?: string }

interface ProofAnswer {
  dataset: string;
  title: string;
  format: 'int' | 'km' | 'kmh' | 'pct' | 'min' | 'num';
  value: number | null;
  method: string[];
  formula: string;
  excluded: { label: string; count: number }[];
  columns: Column[];
  rows: Record<string, any>[];
  total: number;
  page: number;
  page_size: number;
  computed_at: string;
}

const PAGE_SIZE = 25;

export function formatProofValue(v: number | null | undefined, format: ProofAnswer['format']): string {
  if (v == null || !Number.isFinite(v)) return '—';
  switch (format) {
    case 'int': return Math.round(v).toLocaleString('en-IN');
    case 'km': return `${v.toLocaleString('en-IN', { maximumFractionDigits: 2 })} km`;
    case 'kmh': return `${v.toLocaleString('en-IN', { maximumFractionDigits: 2 })} km/h`;
    case 'pct': return `${v.toLocaleString('en-IN', { maximumFractionDigits: 2 })}%`;
    case 'min': return `${v.toLocaleString('en-IN', { maximumFractionDigits: 1 })} min`;
    default: return v.toLocaleString('en-IN', { maximumFractionDigits: 2 });
  }
}

function sameValue(a: number | null | undefined, b: number | null | undefined): boolean {
  if (a == null || !Number.isFinite(a)) return b == null || !Number.isFinite(b as number);
  if (b == null || !Number.isFinite(b)) return false;
  return Math.abs(a - b) < 0.005 + Math.abs(a) * 1e-9;
}

function cell(c: Column, r: Record<string, any>): ReactNode {
  const v = r[c.key];
  if (v == null || v === '') return <span className="text-gray-600">—</span>;
  let text: ReactNode = String(v);
  if (c.kind === 'datetime') {
    const m = String(v).match(/^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})/);
    text = m ? `${m[3]}-${m[2]}-${m[1]} ${m[4]}:${m[5]}` : String(v);
  } else if (c.kind === 'number') {
    text = Number(v).toLocaleString('en-IN', { maximumFractionDigits: c.digits ?? 0, minimumFractionDigits: 0 });
  } else if (c.kind === 'bool') {
    text = Number(v) === 1 ? <span className="text-emerald-400">yes</span> : <span className="text-red-400">no</span>;
  }
  if (c.link) {
    const href = c.link.replace(/\{(\w+)\}/g, (_, k) => encodeURIComponent(String(r[k] ?? '')));
    return <Link to={href} className="text-blue-400 hover:text-blue-300">{text}</Link>;
  }
  return text;
}

export function ProofPanel({ open, onClose }: { open: OpenProof; onClose: () => void }) {
  const [page, setPage] = useState(1);
  const [sort, setSort] = useState<string | null>(null);
  const [order, setOrder] = useState<'asc' | 'desc'>('desc');
  const [data, setData] = useState<ProofAnswer | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [downloading, setDownloading] = useState(false);
  const params = useMemo(() => {
    const p: Record<string, any> = {};
    Object.entries(open.spec.params ?? {}).forEach(([k, v]) => { if (v !== undefined && v !== null && v !== '') p[k] = v; });
    return p;
  }, [open.spec.params]);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    backendApi.get(`/proof/${open.spec.dataset}`, { params: { ...params, page, page_size: PAGE_SIZE, sort: sort ?? undefined, order } })
      .then(res => { if (alive) { setData(res.data); setError(null); } })
      .catch(e => { if (alive) setError(e?.response?.data?.detail || e?.message || 'Could not load the proof'); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [open.spec.dataset, params, page, sort, order]);

  const download = useCallback(async () => {
    setDownloading(true);
    try {
      const res = await backendApi.get(`/proof/${open.spec.dataset}`, {
        params: { ...params, format: 'csv', sort: sort ?? undefined, order }, responseType: 'blob' });
      const url = URL.createObjectURL(res.data as Blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `${open.spec.dataset}.csv`;
      a.click();
      URL.revokeObjectURL(url);
    } finally {
      setDownloading(false);
    }
  }, [open.spec.dataset, params, sort, order]);

  const pages = data ? Math.max(1, Math.ceil(data.total / PAGE_SIZE)) : 1;
  const tileValue = open.spec.value;
  const agrees = data ? sameValue(tileValue, data.value) : true;

  return (
    <section className="bg-gray-900 rounded-xl border border-blue-500/40 p-5 mt-1 mb-2 animate-fade-in" aria-label={`How ${open.label} is calculated`}>
      <div className="flex items-start justify-between gap-3">
        <h3 className="text-base font-semibold text-white flex items-center gap-2">
          <FlaskConical className="w-[18px] h-[18px] text-blue-400" />
          How “{open.label}” is calculated
        </h3>
        <button onClick={onClose} title="Close" className="p-1 rounded text-gray-500 hover:text-gray-200 hover:bg-gray-800">
          <X className="w-4 h-4" />
        </button>
      </div>

      {error && <p className="mt-3 text-sm text-red-300">{error}</p>}
      {!data && !error && <p className="mt-3 text-sm text-gray-500 flex items-center gap-2"><Loader2 className="w-4 h-4 animate-spin" /> Recounting…</p>}

      {data && (
        <div className="mt-3 space-y-4">
          <div className={`flex flex-wrap items-center gap-x-6 gap-y-1 rounded-lg border px-3 py-2 text-sm ${agrees
            ? 'border-emerald-900 bg-emerald-950/20' : 'border-amber-800 bg-amber-950/20'}`}>
            <span className="text-gray-400">On the tile: <span className="text-white font-semibold tabular">{open.display}</span></span>
            <span className="text-gray-400">Recounted from the records below: <span className="text-white font-semibold tabular">{formatProofValue(data.value, data.format)}</span></span>
            {agrees ? (
              <span className="flex items-center gap-1 text-emerald-400"><CheckCircle2 className="w-4 h-4" /> they agree</span>
            ) : (
              <span className="flex items-center gap-1 text-amber-300">
                <TriangleAlert className="w-4 h-4" /> they differ: the tile was computed earlier (tiles are cached up to 10 minutes) and newer records are counted here
              </span>
            )}
            <span className="text-gray-600 ml-auto">recounted {data.computed_at.slice(11, 16)}</span>
          </div>

          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            <div>
              <p className="text-xs uppercase tracking-wide text-gray-500 mb-1.5">What is counted</p>
              <ul className="space-y-1 text-sm text-gray-300 list-disc pl-5">
                {data.method.map((m, i) => <li key={i}>{m}</li>)}
              </ul>
            </div>
            <div className="space-y-3">
              <div>
                <p className="text-xs uppercase tracking-wide text-gray-500 mb-1.5">The arithmetic</p>
                <p className="font-mono text-sm text-gray-200 bg-gray-950 border border-gray-800 rounded-lg px-3 py-2">{data.formula}</p>
              </div>
              <div>
                <p className="text-xs uppercase tracking-wide text-gray-500 mb-1.5">Not counted</p>
                {data.excluded.length === 0 ? (
                  <p className="text-sm text-gray-400">Nothing in the window was left out.</p>
                ) : (
                  <ul className="space-y-1 text-sm text-gray-300">
                    {data.excluded.map((x, i) => (
                      <li key={i} className="flex justify-between gap-3"><span>{x.label}</span><span className="tabular text-amber-300">{x.count.toLocaleString('en-IN')}</span></li>
                    ))}
                  </ul>
                )}
              </div>
            </div>
          </div>

          <div>
            <div className="flex items-center justify-between gap-3 mb-2">
              <p className="text-xs uppercase tracking-wide text-gray-500">
                The records ({data.total.toLocaleString('en-IN')}){loading && <Loader2 className="inline w-3.5 h-3.5 ml-2 animate-spin" />}
              </p>
              <button onClick={download} disabled={downloading || data.total === 0}
                className="flex items-center gap-1.5 px-2.5 py-1 rounded-lg border border-gray-700 bg-gray-800/60 text-sm text-gray-300 hover:text-white disabled:opacity-40">
                {downloading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Download className="w-4 h-4" />} CSV
              </button>
            </div>
            {data.total === 0 ? (
              <p className="text-sm text-gray-500 py-4 text-center">No records in this window.</p>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-left text-xs uppercase tracking-wide text-gray-500 border-b border-gray-800">
                      {data.columns.map(c => (
                        <th key={c.key} className={`py-2 pr-3 font-medium ${c.kind === 'number' ? 'text-right' : ''}`}>
                          <button onClick={() => { if (sort === c.key) setOrder(o => (o === 'asc' ? 'desc' : 'asc')); else { setSort(c.key); setOrder('desc'); } setPage(1); }}
                            className="inline-flex items-center gap-1 hover:text-gray-300">
                            {c.label}
                            {sort === c.key && (order === 'asc' ? <ArrowUp className="w-3 h-3" /> : <ArrowDown className="w-3 h-3" />)}
                          </button>
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {data.rows.map((r, i) => (
                      <tr key={i} className="border-b border-gray-800/60">
                        {data.columns.map(c => (
                          <td key={c.key} className={`py-1.5 pr-3 text-gray-300 ${c.kind === 'number' ? 'text-right tabular' : ''}`}>{cell(c, r)}</td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            {pages > 1 && (
              <div className="flex items-center justify-end gap-2 mt-2 text-sm text-gray-400">
                <button onClick={() => setPage(p => Math.max(1, p - 1))} disabled={page <= 1} className="p-1 rounded hover:bg-gray-800 disabled:opacity-30"><ChevronLeft className="w-4 h-4" /></button>
                <span className="tabular">page {page} of {pages}</span>
                <button onClick={() => setPage(p => Math.min(pages, p + 1))} disabled={page >= pages} className="p-1 rounded hover:bg-gray-800 disabled:opacity-30"><ChevronRight className="w-4 h-4" /></button>
              </div>
            )}
          </div>
        </div>
      )}
    </section>
  );
}

/**
 * A row (or grid) of tiles whose figures open their proof beneath it.
 *   <ProofGrid className="grid grid-cols-6 gap-4"> <KPICard ... proof={{ dataset, params, value }} /> </ProofGrid>
 */
export function ProofGrid({ className = '', children }: { className?: string; children: ReactNode }) {
  const [open, setOpen] = useState<OpenProof | null>(null);
  const toggle = useCallback((p: OpenProof) => setOpen(o => (o?.id === p.id ? null : p)), []);
  const ctx = useMemo(() => ({ open, toggle }), [open, toggle]);
  return (
    <ProofContext.Provider value={ctx}>
      <div className={className}>{children}</div>
      {open && <ProofPanel key={open.id} open={open} onClose={() => setOpen(null)} />}
    </ProofContext.Provider>
  );
}
