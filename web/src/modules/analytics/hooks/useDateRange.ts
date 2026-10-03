import { useCallback, useEffect, useState } from 'react';

/**
 * Dashboard date-range state with a rolling 7-day default, persisted to
 * localStorage. Rolling presets ("7d"/"30d"/"90d") are re-derived on load so
 * "last 7 days" always means the 7 days ending today — not the 7 days that were
 * current when the value was last saved.
 */
export type DatePreset = '7d' | '30d' | '90d' | 'all' | 'custom';

export interface DateRange {
  from: string; // YYYY-MM-DD, '' means unbounded
  to: string;   // YYYY-MM-DD, '' means unbounded
  preset: DatePreset;
}

const STORAGE_KEY = 'st.dateRange.v1';

function ymd(d: Date): string {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${y}-${m}-${day}`;
}

/** Inclusive [from, to] window for a rolling preset ('all' → unbounded). */
export function rangeForPreset(preset: DatePreset): { from: string; to: string } {
  if (preset === 'all' || preset === 'custom') return { from: '', to: '' };
  const days = preset === '30d' ? 30 : preset === '90d' ? 90 : 7;
  const to = new Date();
  const from = new Date();
  from.setDate(to.getDate() - (days - 1)); // inclusive of today → N calendar days
  return { from: ymd(from), to: ymd(to) };
}

const DEFAULT: DateRange = { preset: '7d', ...rangeForPreset('7d') };

function load(): DateRange {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw) {
      const p = JSON.parse(raw) as DateRange;
      if (p.preset && p.preset !== 'custom') return { preset: p.preset, ...rangeForPreset(p.preset) };
      if (p.preset === 'custom' && (p.from || p.to)) return p;
    }
  } catch { /* ignore corrupt storage */ }
  return DEFAULT;
}

export function useDateRange() {
  const [range, setRange] = useState<DateRange>(load);

  useEffect(() => {
    try { localStorage.setItem(STORAGE_KEY, JSON.stringify(range)); } catch { /* ignore */ }
  }, [range]);

  const setPreset = useCallback((preset: DatePreset) => {
    setRange({ preset, ...rangeForPreset(preset) });
  }, []);

  const setCustom = useCallback((from: string, to: string) => {
    setRange({ preset: 'custom', from, to });
  }, []);

  return { ...range, setPreset, setCustom };
}
