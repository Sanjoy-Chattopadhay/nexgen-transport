/**
 * Formatting. Every number on screen goes through one of these, so a missing
 * value always reads as a dash and never as 0 -- "no data" and "none" are
 * different answers and a dashboard that confuses them is lying.
 */

const DASH = '—';

export const isNum = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v);

export function fmtInt(v: number | null | undefined): string {
  return isNum(v) ? Math.round(v).toLocaleString('en-IN') : DASH;
}

export function fmtNum(v: number | null | undefined, digits = 1): string {
  return isNum(v) ? v.toLocaleString('en-IN', { maximumFractionDigits: digits, minimumFractionDigits: 0 }) : DASH;
}

export function fmtPct(v: number | null | undefined, digits = 1): string {
  return isNum(v) ? `${v.toFixed(digits)}%` : DASH;
}

export function share(part: number | null | undefined, whole: number | null | undefined): number | null {
  return isNum(part) && isNum(whole) && whole > 0 ? (100 * part) / whole : null;
}

/** Seconds to a compact human duration: 45s, 12m, 3h 20m, 2d 4h. */
export function fmtDuration(seconds: number | null | undefined): string {
  if (!isNum(seconds)) return DASH;
  const s = Math.abs(seconds);
  const sign = seconds < 0 ? '−' : '';
  if (s < 60) return `${sign}${Math.round(s)}s`;
  if (s < 3600) return `${sign}${Math.round(s / 60)}m`;
  if (s < 86400) {
    const h = Math.floor(s / 3600);
    const m = Math.round((s - h * 3600) / 60);
    return m ? `${sign}${h}h ${m}m` : `${sign}${h}h`;
  }
  const d = Math.floor(s / 86400);
  const h = Math.round((s - d * 86400) / 3600);
  return h ? `${sign}${d}d ${h}h` : `${sign}${d}d`;
}

export function fmtHours(seconds: number | null | undefined, digits = 1): string {
  return isNum(seconds) ? `${(seconds / 3600).toLocaleString('en-IN', { maximumFractionDigits: digits })} h` : DASH;
}

function toDate(v: string | null | undefined): Date | null {
  if (!v) return null;
  const d = new Date(v.includes('T') || v.length <= 10 ? v : v.replace(' ', 'T'));
  return Number.isNaN(+d) ? null : d;
}

export function fmtDateTime(v: string | null | undefined): string {
  const d = toDate(v);
  if (!d) return DASH;
  return d.toLocaleString('en-GB', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit', hour12: false });
}

export function fmtDateTimeFull(v: string | null | undefined): string {
  const d = toDate(v);
  if (!d) return DASH;
  return d.toLocaleString('en-GB', {
    day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
  });
}

export function fmtDate(v: string | null | undefined): string {
  const d = toDate(v);
  if (!d) return DASH;
  return d.toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' });
}

export function fmtDay(v: string | null | undefined): string {
  const d = toDate(v);
  if (!d) return DASH;
  return d.toLocaleDateString('en-GB', { weekday: 'short', day: '2-digit', month: 'short' });
}

/** "12 Jul" -- for chart axes, where a weekday would crowd the labels. */
export function fmtDayShort(v: string | null | undefined): string {
  const d = toDate(v);
  if (!d) return DASH;
  return d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short' });
}

export function fmtTime(v: string | null | undefined): string {
  const d = toDate(v);
  if (!d) return DASH;
  return d.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', hour12: false });
}

export function fmtKm(v: number | null | undefined): string {
  return isNum(v) ? `${v.toLocaleString('en-IN', { maximumFractionDigits: v < 10 ? 1 : 0 })} km` : DASH;
}

export function fmtMetres(v: number | null | undefined): string {
  if (!isNum(v)) return DASH;
  return v >= 1000 ? `${(v / 1000).toFixed(v >= 10000 ? 0 : 1)} km` : `${Math.round(v)} m`;
}

export function fmtArea(m2: number | null | undefined): string {
  if (!isNum(m2)) return DASH;
  if (m2 < 10_000) return `${Math.round(m2).toLocaleString('en-IN')} m²`;
  if (m2 < 1_000_000) return `${(m2 / 10_000).toFixed(1)} ha`;
  return `${(m2 / 1_000_000).toLocaleString('en-IN', { maximumFractionDigits: m2 < 1e8 ? 2 : 0 })} km²`;
}

export function isoDay(d: Date): string {
  const z = new Date(d.getTime() - d.getTimezoneOffset() * 60000);
  return z.toISOString().slice(0, 10);
}

export function shiftDay(day: string, delta: number): string {
  const d = new Date(`${day}T00:00:00`);
  d.setDate(d.getDate() + delta);
  return isoDay(d);
}

export const SCALE_LABEL: Record<string, string> = {
  micro: 'Micro · <1 ha', site: 'Site · <1 km²', campus: 'Campus · <100 km²', regional: 'Regional',
};

export const KIND_LABEL: Record<string, string> = {
  overspeed: 'Overspeed in site',
  restricted_entry: 'Restricted zone entry',
  high_risk_entry: 'High-risk zone entry',
};

export const QUALITY_TEXT: Record<string, string> = {
  good: 'Dense, continuous trail',
  sparse: 'Thin, or the truck moved during a 15+ min hole',
  noisy: 'Over 10% of fixes refused or corrected',
  broken: 'The truck moved during an hour-long hole',
  no_gps: 'No usable GPS',
};
