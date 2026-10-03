/**
 * Colour themes for the whole product.
 *
 *   teal     the charcoal-and-teal palette (the default)
 *   classic  the original blue-grey, kept intact as the fallback
 *
 * To go back to the previous look for everyone, set DEFAULT_THEME to
 * 'classic' and rebuild. The sidebar toggle switches one browser and
 * remembers the choice there.
 *
 * How it works. Tailwind v4 compiles every colour utility to a CSS variable
 * (`bg-gray-900` is `var(--color-gray-900)`), so index.css re-points those
 * variables under `:root[data-theme='teal']` and every class follows. What CSS
 * cannot reach -- chart series, map layers, colours drawn in JavaScript -- has
 * two routes:
 *
 *   PALETTE  semantic colours (series, grid, tooltip, map) for code written
 *            against the theme;
 *   tc(hex)  for code that names a Tailwind colour by its hex value (most of
 *            the fleet-analytics charts): returns the colour that same
 *            Tailwind token has under the active theme, so a chart's
 *            `#3b82f6` is exactly what `text-blue-500` is on the same page.
 *            Distinct inputs stay distinct, so categorical series never merge.
 *
 * Both are fixed when this module is first evaluated, from the stored choice,
 * and a switch reloads the page. Colours captured at module level (constant
 * tables, chart configs) are therefore always the active theme's, which a
 * re-render alone could not guarantee.
 */
import { createContext, useContext } from 'react';

export type ThemeName = 'teal' | 'classic';

export const DEFAULT_THEME: ThemeName = 'teal';

export interface Palette {
  // chart series and semantic marks
  blue: string; cyan: string; green: string; amber: string; red: string; purple: string; gray: string;
  /** Colour set against `blue` in the same chart (vehicles line, second series). */
  pair: string;
  /** A third series beside `blue` and `pair` (departures beside arrivals). */
  pairAlt: string;
  grid: string; axis: string; legend: string; categoryTick: string; cursor: string;
  tooltipBg: string; tooltipBorder: string; tooltipText: string;
  heat: [number, number, number]; heatEmpty: string;
  // map
  mapFill: string; mapStroke: string; mapDistrict: string;
  fence: { restricted: string; high_risk: string; micro: string; site: string; campus: string; regional: string };
  markerOutline: string; rawTrack: string; stopped: string;
}

export const THEMES: Record<ThemeName, Palette> = {
  classic: {
    blue: '#3b82f6', cyan: '#06b6d4', green: '#10b981', amber: '#f59e0b', red: '#ef4444', purple: '#8b5cf6',
    gray: '#6b7280', pair: '#10b981', pairAlt: '#06b6d4',
    grid: '#1f2937', axis: '#6b7280', legend: '#9ca3af', categoryTick: '#cbd5e1', cursor: 'rgba(148,163,184,0.08)',
    tooltipBg: '#111827', tooltipBorder: '#374151', tooltipText: '#e5e7eb',
    heat: [59, 130, 246], heatEmpty: '#111827',
    mapFill: '#1a2436', mapStroke: '#3b4a60', mapDistrict: '#475569',
    fence: { restricted: '#ef4444', high_risk: '#f59e0b', micro: '#22d3ee', site: '#3b82f6', campus: '#a78bfa', regional: '#64748b' },
    markerOutline: '#0b1220', rawTrack: '#94a3b8', stopped: '#9ca3af',
  },
  teal: {
    // Primary accent teal, secondary accent cyan, success emerald, warning
    // amber, error coral. Lavender and a soft blue are added only where a
    // chart needs a second hue that cannot be mistaken for teal.
    blue: '#5EEAD4', cyan: '#67E8F9', green: '#6EE7B7', amber: '#FCD34D', red: '#FCA5A5', purple: '#C4B5FD',
    gray: '#687386', pair: '#C4B5FD', pairAlt: '#93C5FD',
    grid: '#28303D', axis: '#687386', legend: '#9AA4B2', categoryTick: '#C2C9D4', cursor: 'rgba(94,234,212,0.06)',
    tooltipBg: '#151922', tooltipBorder: '#28303D', tooltipText: '#E8ECF2',
    heat: [94, 234, 212], heatEmpty: '#151922',
    mapFill: '#1E2430', mapStroke: '#3D4656', mapDistrict: '#4A5363',
    fence: { restricted: '#FCA5A5', high_risk: '#FCD34D', micro: '#67E8F9', site: '#5EEAD4', campus: '#C4B5FD', regional: '#687386' },
    markerOutline: '#0F1115', rawTrack: '#9AA4B2', stopped: '#9AA4B2',
  },
};

/**
 * Tailwind's palette by hex (the values the fleet-analytics charts were
 * written with) -> the value index.css gives that same token under teal.
 * Only the tokens the teal theme re-points are listed; every other colour is
 * the same in both themes, as it is in CSS.
 */
const TEAL_BY_HEX: Record<string, string> = {
  '#ffffff': '#E8ECF2',
  // gray
  '#f9fafb': '#F4F6F9', '#f3f4f6': '#E8ECF2', '#e5e7eb': '#DCE1E9', '#d1d5db': '#C2C9D4',
  '#9ca3af': '#9AA4B2', '#6b7280': '#687386', '#4b5563': '#566073', '#374151': '#343C4B',
  '#1f2937': '#28303D', '#111827': '#1B202B', '#030712': '#0F1115',
  // blue: the accent role, so it becomes teal
  '#93c5fd': '#99F6E4', '#60a5fa': '#5EEAD4', '#3b82f6': '#2DD4BF', '#2563eb': '#5EEAD4',
  '#1d4ed8': '#14B8A6', '#1e40af': '#115E59', '#1e3a8a': '#134E4A', '#172554': '#0C2927',
  // cyan
  '#67e8f9': '#A5F3FC', '#22d3ee': '#67E8F9', '#06b6d4': '#22D3EE', '#164e63': '#164E63', '#083344': '#0B2730',
  // emerald
  '#6ee7b7': '#A7F3D0', '#34d399': '#6EE7B7', '#10b981': '#34D399', '#064e3b': '#064E3B', '#022c22': '#0A261E',
  // amber
  '#fcd34d': '#FDE68A', '#fbbf24': '#FCD34D', '#f59e0b': '#FBBF24', '#d97706': '#D97706',
  '#b45309': '#92400E', '#78350f': '#78350F', '#451a03': '#2A1F0A',
  // red
  '#fca5a5': '#FECACA', '#f87171': '#FCA5A5', '#ef4444': '#F87171', '#7f1d1d': '#7F1D1D', '#450a0a': '#2B1518',
  // purple
  '#d8b4fe': '#DDD6FE', '#c084fc': '#C4B5FD', '#a855f7': '#A78BFA', '#581c87': '#4C1D95', '#3b0764': '#1D1630',
};

const STORAGE_KEY = 'nexgen.theme';

export function storedTheme(): ThemeName {
  try {
    const v = window.localStorage.getItem(STORAGE_KEY);
    if (v === 'teal' || v === 'classic') return v;
  } catch { /* storage unavailable: fall back to the default */ }
  return DEFAULT_THEME;
}

/** The theme this page load is drawn in. */
export const ACTIVE_THEME: ThemeName = storedTheme();

/** The live palette for code written against the theme. */
export const PALETTE: Palette = { ...THEMES[ACTIVE_THEME], fence: { ...THEMES[ACTIVE_THEME].fence } };

/** A Tailwind colour named by hex, as that token looks in the active theme. */
export function tc(hex: string): string {
  if (ACTIVE_THEME === 'classic') return hex;
  return TEAL_BY_HEX[hex.toLowerCase()] ?? hex;
}

/** Put the active theme on the document (before the first render). */
export function applyTheme(): void {
  document.documentElement.dataset.theme = ACTIVE_THEME;
}

/** Remember a theme and redraw everything in it. */
export function switchTheme(name: ThemeName): void {
  if (name === ACTIVE_THEME) return;
  try { window.localStorage.setItem(STORAGE_KEY, name); } catch { /* not persisted */ }
  window.location.reload();
}

export const ThemeContext = createContext<{ theme: ThemeName; setTheme: (t: ThemeName) => void }>({
  theme: ACTIVE_THEME, setTheme: switchTheme,
});

export const useTheme = () => useContext(ThemeContext);
