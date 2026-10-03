/** Formatting for the developer page. Times are the server's wall clock (IST). */

export function fmtUptime(s: number | null | undefined): string {
  if (s == null) return '—';
  if (s < 60) return `${Math.round(s)} s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m} min`;
  const h = Math.floor(m / 60);
  if (h < 48) return `${h} h ${m % 60} min`;
  return `${Math.floor(h / 24)} d ${h % 24} h`;
}

export function fmtSeconds(s: number | null | undefined): string {
  if (s == null) return '—';
  if (s < 1) return `${Math.round(s * 1000)} ms`;
  if (s < 120) return `${s.toFixed(1)} s`;
  return fmtUptime(s);
}

export function fmtBytes(b: number | null | undefined): string {
  if (b == null) return '—';
  if (b < 1024 * 1024) return `${(b / 1024).toFixed(0)} KB`;
  if (b < 1024 ** 3) return `${(b / 1024 ** 2).toFixed(1)} MB`;
  return `${(b / 1024 ** 3).toFixed(2)} GB`;
}

export function fmtInt(n: number | null | undefined): string {
  return n == null ? '—' : n.toLocaleString('en-IN');
}

/** "2026-10-03T21:56:55.49" -> "03 Oct 21:56:55" (wall clock, no zone shift). */
export function fmtWhen(v: string | null | undefined): string {
  if (!v) return '—';
  const m = String(v).match(/^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2}):(\d{2})/);
  if (!m) return String(v);
  const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  return `${m[3]} ${months[Number(m[2]) - 1]} ${m[4]}:${m[5]}:${m[6]}`;
}

/** How long ago a wall-clock time was, against the browser's clock. */
export function fmtAgo(v: string | null | undefined): string {
  if (!v) return '—';
  const t = new Date(String(v).replace(' ', 'T').replace(/\+\d{2}:\d{2}$/, '')).getTime();
  if (Number.isNaN(t)) return fmtWhen(v);
  const s = (Date.now() - t) / 1000;
  if (s < 0) return `in ${fmtUptime(-s)}`;
  return `${fmtUptime(s)} ago`;
}
