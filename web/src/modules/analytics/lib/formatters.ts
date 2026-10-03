export function formatNumber(n: number | null | undefined): string {
  if (n == null) return '-';
  return n.toLocaleString('en-IN');
}

export function formatDuration(minutes: number | null | undefined): string {
  if (minutes == null) return '-';
  if (minutes < 60) return `${Math.round(minutes)}m`;
  const h = Math.floor(minutes / 60);
  const m = Math.round(minutes % 60);
  return m > 0 ? `${h}h ${m}m` : `${h}h`;
}

export function formatDistance(km: number | null | undefined): string {
  if (km == null) return '-';
  return `${km.toLocaleString('en-IN', { maximumFractionDigits: 1 })} km`;
}

export function formatPercent(val: number | null | undefined): string {
  if (val == null) return '-';
  return `${val.toFixed(1)}%`;
}

export function formatSpeed(kmph: number | null | undefined): string {
  if (kmph == null) return '-';
  return `${kmph.toFixed(1)} km/h`;
}

export function formatDate(dt: string | null | undefined): string {
  if (!dt) return '-';
  try {
    return new Date(dt).toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: 'numeric' });
  } catch { return dt; }
}

export function formatDateTime(dt: string | null | undefined): string {
  if (!dt) return '-';
  try {
    return new Date(dt).toLocaleString('en-IN', { day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' });
  } catch { return dt; }
}

/**
 * On-time-rate colour bands, with an explicit band for "unknown".
 *
 * A null rate means no trip in the group has a recorded outcome — it is not
 * 0%. Comparing directly is a trap: `null >= 90` is false, so every unknown
 * rate falls through to the worst band and missing data renders as a red
 * failure. These helpers give it a neutral tone instead.
 */
export function rateTextClass(val: number | null | undefined, hi = 90, lo = 80): string {
  if (val == null) return 'text-gray-500';
  return val >= hi ? 'text-emerald-400' : val >= lo ? 'text-amber-400' : 'text-red-400';
}

export function rateKpiColor(val: number | null | undefined, hi = 90, lo = 80): string {
  if (val == null) return 'blue';
  return val >= hi ? 'green' : val >= lo ? 'amber' : 'red';
}

export function rateBadgeVariant(val: number | null | undefined, hi = 90, lo = 80): string {
  if (val == null) return 'neutral';
  return val >= hi ? 'success' : val >= lo ? 'warning' : 'danger';
}
