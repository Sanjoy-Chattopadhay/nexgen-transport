/**
 * The shared UI kit. Styled to match Smart-Truck -- same dark ground, card
 * shape, KPI tiles and table rhythm -- so the two applications read as one
 * product family.
 */
import { useContext, useEffect, useId, useState, type ReactNode } from 'react';
import { Link } from 'react-router-dom';
import type { LucideIcon } from 'lucide-react';
import {
  AlertTriangle, ArrowDown, ArrowLeft, ArrowUp, ArrowUpDown, ChevronDown, ChevronLeft, ChevronRight,
  Info, Loader2, Search, X,
} from 'lucide-react';
import { KPIGridContext, type DrillSpec } from './kpiContext';

// ---------------------------------------------------------------------------
// Cards
// ---------------------------------------------------------------------------

export function Card({ title, subtitle, icon: Icon, iconClass = 'text-blue-400', actions, children,
  className = '', pad = true }: {
  title?: ReactNode; subtitle?: ReactNode; icon?: LucideIcon; iconClass?: string;
  actions?: ReactNode; children: ReactNode; className?: string; pad?: boolean;
}) {
  return (
    <section className={`bg-gray-900 rounded-xl border border-gray-800 ${pad ? 'p-5' : ''} ${className}`}>
      {(title || actions) && (
        <div className={`flex items-start justify-between gap-3 ${pad ? '' : 'px-5 pt-5'} ${subtitle ? 'mb-1' : 'mb-4'}`}>
          {title && (
            <h2 className="text-base font-semibold text-white flex items-center gap-2 min-w-0">
              {Icon && <Icon className={`w-[18px] h-[18px] shrink-0 ${iconClass}`} />}
              <span className="truncate">{title}</span>
            </h2>
          )}
          {actions && <div className="flex items-center gap-2 shrink-0">{actions}</div>}
        </div>
      )}
      {subtitle && <p className={`text-xs text-gray-500 mb-4 max-w-4xl ${pad ? '' : 'px-5'}`}>{subtitle}</p>}
      {children}
    </section>
  );
}

const KPI_STYLES: Record<string, { bg: string; text: string; icon: string }> = {
  blue: { bg: 'bg-blue-950/50', text: 'text-blue-400', icon: 'text-blue-500' },
  green: { bg: 'bg-emerald-950/50', text: 'text-emerald-400', icon: 'text-emerald-500' },
  amber: { bg: 'bg-amber-950/50', text: 'text-amber-400', icon: 'text-amber-500' },
  red: { bg: 'bg-red-950/50', text: 'text-red-400', icon: 'text-red-500' },
  purple: { bg: 'bg-purple-950/50', text: 'text-purple-400', icon: 'text-purple-500' },
  cyan: { bg: 'bg-cyan-950/50', text: 'text-cyan-400', icon: 'text-cyan-500' },
  gray: { bg: 'bg-gray-900', text: 'text-gray-200', icon: 'text-gray-500' },
};

/**
 * A headline number. Given `drill` (records behind it, from the API) or
 * `details` (something the page already holds), the tile opens a dropdown
 * beneath its KPIGrid that answers "which ones?".
 */
export function KPI({ label, value, icon: Icon, color = 'blue', hint, delta, deltaGood = 'up', drill, details }: {
  label: string; value: ReactNode; icon: LucideIcon; color?: string; hint?: ReactNode;
  /** Percentage change against a baseline; colour depends on which way is good. */
  delta?: number | null; deltaGood?: 'up' | 'down' | 'none';
  drill?: DrillSpec; details?: ReactNode | (() => ReactNode);
}) {
  const s = KPI_STYLES[color] || KPI_STYLES.blue;
  const showDelta = typeof delta === 'number' && Number.isFinite(delta);
  const good = !showDelta || deltaGood === 'none' ? null : deltaGood === 'up' ? delta! >= 0 : delta! <= 0;
  const grid = useContext(KPIGridContext);
  const id = useId();
  const can = !!grid && !!(drill || details);
  const isOpen = can && grid!.open?.id === id;
  const Tag = can ? 'button' : 'div';
  return (
    <Tag {...(can ? {
      type: 'button' as const, 'aria-expanded': isOpen,
      title: isOpen ? 'Close' : `What makes up “${label}”`,
      onClick: () => grid!.toggle({ id, label, value, drill, details }),
    } : {})}
      className={`${s.bg} rounded-xl border p-4 min-w-0 text-left relative transition-colors ${
        isOpen ? 'border-blue-500/60 ring-1 ring-blue-500/30' : 'border-gray-800'} ${
        can ? 'cursor-pointer hover:border-gray-700 group' : ''}`}>
      {can && (
        <ChevronDown className={`w-3.5 h-3.5 absolute top-3 right-3 transition-transform ${
          isOpen ? 'rotate-180 text-blue-400' : 'text-gray-600 group-hover:text-gray-400'}`} />
      )}
      <div className={`flex items-center gap-1.5 min-w-0 ${can ? 'pr-4' : ''}`}>
        <Icon className={`w-4 h-4 ${s.icon} opacity-80 shrink-0`} />
        <p className="text-xs text-gray-400 truncate" title={label}>{label}</p>
      </div>
      <p className={`text-2xl font-bold ${s.text} tabular leading-tight mt-1.5 whitespace-nowrap`}>{value}</p>
      {(hint || showDelta) && (
        <div className="mt-1.5 flex items-center gap-2 text-xs text-gray-500 min-w-0">
          {showDelta && (
            <span className={`font-medium tabular ${good === null ? 'text-gray-400' : good ? 'text-emerald-400' : 'text-red-400'}`}>
              {delta! >= 0 ? '▲' : '▼'} {Math.abs(delta!).toFixed(0)}%
            </span>
          )}
          {hint && <span className="truncate">{hint}</span>}
        </div>
      )}
    </Tag>
  );
}

export function Field({ label, value, mono }: { label: string; value: ReactNode; mono?: boolean }) {
  return (
    <div className="bg-gray-800/50 rounded-lg p-3 min-w-0">
      <p className="text-xs text-gray-500 mb-1">{label}</p>
      <p className={`text-sm text-gray-200 break-words ${mono ? 'font-mono text-xs' : ''}`}>{value ?? '—'}</p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Badges
// ---------------------------------------------------------------------------

const BADGE: Record<string, string> = {
  success: 'bg-emerald-900/40 text-emerald-400',
  warning: 'bg-amber-900/40 text-amber-400',
  danger: 'bg-red-900/40 text-red-400',
  info: 'bg-blue-900/40 text-blue-400',
  purple: 'bg-purple-900/40 text-purple-300',
  cyan: 'bg-cyan-900/40 text-cyan-300',
  neutral: 'bg-gray-800 text-gray-400',
};

export function Badge({ children, variant = 'neutral', title }: {
  children: ReactNode; variant?: keyof typeof BADGE | string; title?: string;
}) {
  return (
    <span title={title}
      className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium whitespace-nowrap ${BADGE[variant] || BADGE.neutral}`}>
      {children}
    </span>
  );
}

export function ScaleBadge({ scale }: { scale?: string | null }) {
  if (!scale) return <span className="text-gray-600">—</span>;
  const v = { micro: 'cyan', site: 'info', campus: 'purple', regional: 'neutral' }[scale] || 'neutral';
  const title = {
    micro: 'Under 1 hectare: a gate, weighbridge, bay',
    site: 'Under 1 km²: a facility',
    campus: 'Under 100 km²: a works or industrial belt',
    regional: 'A district-scale catchment, not a facility',
  }[scale];
  return <Badge variant={v} title={title}>{scale}</Badge>;
}

export function CategoryBadge({ category }: { category?: string | null }) {
  if (!category || category === 'normal') return <Badge>normal</Badge>;
  return <Badge variant={category === 'restricted' ? 'danger' : 'warning'}>{category.replace('_', ' ')}</Badge>;
}

export function QualityBadge({ quality, reason }: { quality?: string | null; reason?: string | null }) {
  if (!quality) return <span className="text-gray-600">—</span>;
  const v = { good: 'success', sparse: 'info', noisy: 'warning', broken: 'danger', no_gps: 'neutral' }[quality] || 'neutral';
  return <Badge variant={v} title={reason || undefined}>{quality.replace('_', ' ')}</Badge>;
}

export function ConfidenceBadge({ level }: { level?: string | null }) {
  const v = { high: 'success', medium: 'warning', low: 'neutral' }[level || ''] || 'neutral';
  return <Badge variant={v}>{level || '—'} confidence</Badge>;
}

// ---------------------------------------------------------------------------
// States
// ---------------------------------------------------------------------------

export function Spinner({ label }: { label?: string }) {
  return (
    <div className="flex items-center justify-center gap-2 py-10 text-gray-500 text-sm">
      <Loader2 className="w-5 h-5 animate-spin" /> {label}
    </div>
  );
}

export function ErrorBox({ error, onRetry }: { error: string; onRetry?: () => void }) {
  return (
    <div className="rounded-lg border border-red-900/50 bg-red-950/20 p-4 flex items-start gap-3">
      <AlertTriangle className="w-5 h-5 text-red-400 shrink-0 mt-0.5" />
      <div className="text-sm">
        <p className="text-red-300 font-medium">Could not load this</p>
        <p className="text-gray-400 mt-0.5 break-words">{error}</p>
        {onRetry && (
          <button onClick={onRetry} className="mt-2 text-xs text-blue-400 hover:text-blue-300">Try again</button>
        )}
      </div>
    </div>
  );
}

export function Note({ children, tone = 'info', title }: {
  children: ReactNode; tone?: 'info' | 'warn' | 'good'; title?: string;
}) {
  const styles = {
    info: 'border-blue-900/50 bg-blue-950/20 text-blue-300',
    warn: 'border-amber-900/50 bg-amber-950/20 text-amber-300',
    good: 'border-emerald-900/50 bg-emerald-950/20 text-emerald-300',
  }[tone];
  return (
    <div className={`rounded-lg border p-3 flex items-start gap-2.5 ${styles}`}>
      <Info className="w-4 h-4 shrink-0 mt-0.5" />
      <div className="text-xs text-gray-400 leading-relaxed">
        {title && <span className="font-medium mr-1" style={{ color: 'inherit' }}>{title}</span>}
        {children}
      </div>
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="py-8 text-center text-sm text-gray-500">{children}</p>;
}

// ---------------------------------------------------------------------------
// Page chrome
// ---------------------------------------------------------------------------

export function PageHeader({ title, subtitle, back, actions, badges }: {
  title: ReactNode; subtitle?: ReactNode; back?: { to: string; label: string };
  actions?: ReactNode; badges?: ReactNode;
}) {
  return (
    <div className="mb-6">
      {back && (
        <Link to={back.to} className="inline-flex items-center gap-1.5 text-sm text-gray-400 hover:text-gray-200 mb-3">
          <ArrowLeft className="w-4 h-4" /> {back.label}
        </Link>
      )}
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div className="min-w-0">
          <div className="flex items-center gap-3 flex-wrap">
            <h1 className="text-2xl font-bold text-white break-words">{title}</h1>
            {badges}
          </div>
          {subtitle && <p className="text-sm text-gray-500 mt-1 max-w-4xl">{subtitle}</p>}
        </div>
        {actions && <div className="flex items-center gap-2 flex-wrap">{actions}</div>}
      </div>
    </div>
  );
}

export function Toolbar({ children }: { children: ReactNode }) {
  return <div className="flex items-center gap-2 flex-wrap">{children}</div>;
}

// ---------------------------------------------------------------------------
// Inputs
// ---------------------------------------------------------------------------

export function SearchInput({ value, onChange, placeholder = 'Search…', width = 'w-64' }: {
  value: string; onChange: (v: string) => void; placeholder?: string; width?: string;
}) {
  const [v, setV] = useState(value);
  useEffect(() => setV(value), [value]);
  useEffect(() => {
    if (v === value) return;
    const id = window.setTimeout(() => onChange(v), 300);
    return () => window.clearTimeout(id);
  }, [v]); // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <div className={`relative ${width}`}>
      <Search className="w-4 h-4 text-gray-500 absolute left-3 top-1/2 -translate-y-1/2" />
      <input value={v} onChange={e => setV(e.target.value)} placeholder={placeholder}
        className="w-full bg-field border border-gray-700 rounded-lg text-sm text-gray-200 pl-9 pr-8 py-2 focus:outline-none focus:border-blue-500" />
      {v && (
        <button onClick={() => { setV(''); onChange(''); }}
          className="absolute right-2 top-1/2 -translate-y-1/2 text-gray-500 hover:text-gray-300">
          <X className="w-4 h-4" />
        </button>
      )}
    </div>
  );
}

export function Select({ value, onChange, options, label }: {
  value: string; onChange: (v: string) => void; label?: string;
  options: { value: string; label: string }[];
}) {
  return (
    <label className="flex items-center gap-2 text-xs text-gray-500">
      {label}
      <select value={value} onChange={e => onChange(e.target.value)}
        className="bg-field border border-gray-700 rounded-lg text-sm text-gray-200 px-3 py-2 focus:outline-none focus:border-blue-500 max-w-[220px]">
        {options.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
      </select>
    </label>
  );
}

export function DateInput({ value, onChange, label }: { value: string; onChange: (v: string) => void; label: string }) {
  return (
    <label className="flex items-center gap-2 text-xs text-gray-500">
      {label}
      <input type="date" value={value} onChange={e => onChange(e.target.value)}
        className="bg-field border border-gray-700 rounded-lg text-sm text-gray-200 px-2.5 py-1.5 focus:outline-none focus:border-blue-500 [color-scheme:dark]" />
    </label>
  );
}

export function Segmented<T extends string>({ value, onChange, options }: {
  value: T; onChange: (v: T) => void; options: { value: T; label: string; icon?: LucideIcon }[];
}) {
  return (
    <div className="flex gap-1 bg-gray-900 border border-gray-800 rounded-lg p-1">
      {options.map(o => (
        <button key={o.value} onClick={() => onChange(o.value)}
          className={`flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium transition-colors ${
            value === o.value ? 'bg-blue-600/15 text-blue-400' : 'text-gray-400 hover:text-gray-200'}`}>
          {o.icon && <o.icon className="w-3.5 h-3.5" />}{o.label}
        </button>
      ))}
    </div>
  );
}

export function Toggle({ checked, onChange, label }: { checked: boolean; onChange: (v: boolean) => void; label: string }) {
  return (
    <label className="inline-flex items-center gap-1.5 text-xs text-gray-400 cursor-pointer select-none">
      <input type="checkbox" checked={checked} onChange={e => onChange(e.target.checked)} className="accent-blue-600" />
      {label}
    </label>
  );
}

// ---------------------------------------------------------------------------
// Table
// ---------------------------------------------------------------------------

// Literal class names: Tailwind only generates classes it can find in source.
const ALIGN = { left: 'text-left', right: 'text-right', center: 'text-center' } as const;

export interface Column<T> {
  key: string;
  label: ReactNode;
  render?: (row: T) => ReactNode;
  align?: 'left' | 'right' | 'center';
  sortable?: boolean;
  className?: string;
  title?: string;
}

export function DataTable<T extends Record<string, any>>({
  columns, rows, loading, onRowClick, sort, order, onSort, empty = 'Nothing to show', dense, rowKey,
}: {
  columns: Column<T>[]; rows: T[]; loading?: boolean; onRowClick?: (row: T) => void;
  sort?: string; order?: 'asc' | 'desc'; onSort?: (key: string) => void; empty?: ReactNode;
  dense?: boolean; rowKey?: (row: T, i: number) => string | number;
}) {
  const pad = dense ? 'px-3 py-2' : 'px-4 py-2.5';
  return (
    <div className="overflow-x-auto relative">
      {loading && rows.length > 0 && (
        <div className="absolute inset-0 bg-gray-900/40 flex items-start justify-center pt-10 z-10">
          <Loader2 className="w-5 h-5 animate-spin text-gray-400" />
        </div>
      )}
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-gray-800">
            {columns.map(c => {
              const active = sort === c.key;
              const can = c.sortable && onSort;
              return (
                <th key={c.key} title={c.title}
                  onClick={() => can && onSort!(c.key)}
                  className={`${pad} ${ALIGN[c.align || 'left']} text-xs font-medium text-gray-400 uppercase tracking-wider whitespace-nowrap ${
                    can ? 'cursor-pointer hover:text-gray-200 select-none' : ''}`}>
                  <span className={`inline-flex items-center gap-1 ${c.align === 'right' ? 'flex-row-reverse' : ''}`}>
                    {c.label}
                    {can && (active
                      ? (order === 'asc' ? <ArrowUp className="w-3 h-3" /> : <ArrowDown className="w-3 h-3" />)
                      : <ArrowUpDown className="w-3 h-3 opacity-30" />)}
                  </span>
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>
          {loading && rows.length === 0 ? (
            <tr><td colSpan={columns.length}><Spinner /></td></tr>
          ) : rows.length === 0 ? (
            <tr><td colSpan={columns.length} className="px-4 py-10 text-center text-gray-500 text-sm">{empty}</td></tr>
          ) : rows.map((r, i) => (
            <tr key={rowKey ? rowKey(r, i) : i} onClick={() => onRowClick?.(r)}
              className={`border-b border-gray-800/50 transition-colors ${onRowClick ? 'cursor-pointer hover:bg-gray-800/50' : ''}`}>
              {columns.map(c => (
                <td key={c.key} className={`${pad} text-gray-300 ${ALIGN[c.align || 'left']} ${c.className || ''}`}>
                  {c.render ? c.render(r) : (r[c.key] ?? '—')}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function Pagination({ page, pages, total, onPage }: {
  page: number; pages: number; total?: number; onPage: (p: number) => void;
}) {
  if (pages <= 1) {
    return total != null ? <p className="text-xs text-gray-500 mt-3">{total.toLocaleString('en-IN')} rows</p> : null;
  }
  return (
    <div className="flex items-center justify-between mt-4 text-xs text-gray-500">
      <span>{total != null && `${total.toLocaleString('en-IN')} rows · `}page {page} of {pages}</span>
      <div className="flex items-center gap-1">
        <button disabled={page <= 1} onClick={() => onPage(page - 1)}
          className="p-1.5 rounded-md bg-gray-800 hover:bg-gray-700 disabled:opacity-30"><ChevronLeft className="w-4 h-4" /></button>
        <button disabled={page >= pages} onClick={() => onPage(page + 1)}
          className="p-1.5 rounded-md bg-gray-800 hover:bg-gray-700 disabled:opacity-30"><ChevronRight className="w-4 h-4" /></button>
      </div>
    </div>
  );
}

/** Sort state for server- or client-sorted tables. */
export function useSort(initial: string, initialOrder: 'asc' | 'desc' = 'desc') {
  const [sort, setSort] = useState(initial);
  const [order, setOrder] = useState<'asc' | 'desc'>(initialOrder);
  const onSort = (key: string) => {
    if (key === sort) setOrder(o => (o === 'asc' ? 'desc' : 'asc'));
    else { setSort(key); setOrder('desc'); }
  };
  return { sort, order, onSort };
}

export function EntityLink({ to, children, className = '' }: { to: string; children: ReactNode; className?: string }) {
  return (
    <Link to={to} onClick={e => e.stopPropagation()}
      className={`text-blue-400 hover:text-blue-300 hover:underline underline-offset-2 ${className}`}>
      {children}
    </Link>
  );
}

/**
 * A trip number, and the other consignment trips that carried the same GPS.
 * The fleet system opens one trip per consignment, so a truck with three
 * invoices is three trips over one set of fixes; `trips` is a comma list that
 * may include `trip` itself.
 */
export function TripRef({ trip, trips, strong = false }: { trip: number; trips?: string | null; strong?: boolean }) {
  const others = (trips || '').split(',').map(x => x.trim()).filter(x => x && x !== String(trip));
  return (
    <span className={`text-blue-400 whitespace-nowrap ${strong ? 'font-medium' : ''}`}
      title={others.length ? `Same truck, same GPS as trip${others.length > 1 ? 's' : ''} ${others.join(', ')}` : undefined}>
      {trip}
      {others.length > 0 && <span className="text-xs font-normal text-gray-500"> +{others.length}</span>}
    </span>
  );
}
