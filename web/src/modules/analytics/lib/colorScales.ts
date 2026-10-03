/** Color ramps for heatmaps / metric-encoded charts (dark theme friendly). */
import { tc } from '../../../core/theme';

/** A Tailwind colour as the active theme draws it, as an RGB triple. */
function rgb(hex: string): [number, number, number] {
  const h = tc(hex).replace('#', '');
  return [parseInt(h.slice(0, 2), 16), parseInt(h.slice(2, 4), 16), parseInt(h.slice(4, 6), 16)];
}

function lerp(a: number, b: number, t: number) { return a + (b - a) * t; }

function rampHex(stops: [number, number, number][], t: number): string {
  const clamped = Math.max(0, Math.min(1, t));
  const seg = clamped * (stops.length - 1);
  const i = Math.min(Math.floor(seg), stops.length - 2);
  const f = seg - i;
  const [r, g, b] = [0, 1, 2].map(k => Math.round(lerp(stops[i][k], stops[i + 1][k], f)));
  return `rgb(${r},${g},${b})`;
}

const RED_YELLOW_GREEN: [number, number, number][] = [
  [249, 65, 68], [249, 199, 79], [144, 190, 109],
];
// blue-950 -> blue-700 -> blue-400: the accent ramp, so it follows the theme.
const BLUES: [number, number, number][] = [rgb('#172554'), rgb('#1d4ed8'), rgb('#60a5fa')];
const VIRIDIS: [number, number, number][] = [
  [68, 1, 84], [33, 145, 140], [253, 231, 37],
];
// red-500 -> gray-800 -> blue-500
const RED_BLUE: [number, number, number][] = [rgb('#ef4444'), rgb('#1f2937'), rgb('#3b82f6')];

export type ColorScheme = 'rdylgn' | 'rdylgn_r' | 'blues' | 'viridis' | 'rdbu';

/** t in [0,1] -> css color for the scheme. */
export function scaleColor(t: number, scheme: ColorScheme): string {
  switch (scheme) {
    case 'rdylgn': return rampHex(RED_YELLOW_GREEN, t);
    case 'rdylgn_r': return rampHex(RED_YELLOW_GREEN, 1 - t);
    case 'blues': return rampHex(BLUES, t);
    case 'viridis': return rampHex(VIRIDIS, t);
    case 'rdbu': return rampHex(RED_BLUE, t);
  }
}

/** Red->green ramp for a metric value within [lo, hi] (invert for cost metrics). */
export function metricColor(v: number | null | undefined, lo: number, hi: number, invert = false): string {
  if (v == null || !isFinite(v) || hi === lo) return tc('#6b7280');
  let t = (v - lo) / (hi - lo);
  if (invert) t = 1 - t;
  return rampHex(RED_YELLOW_GREEN, t);
}
