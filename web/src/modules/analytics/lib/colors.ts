import { tc } from '../../../core/theme';
export const CHART_COLORS = {
  primary: tc('#3b82f6'),
  secondary: tc('#10b981'),
  tertiary: tc('#f59e0b'),
  quaternary: tc('#ef4444'),
  fifth: '#8b5cf6',
  sixth: tc('#06b6d4'),
  grid: tc('#1f2937'),
  tooltipBg: tc('#111827'),
};

export const KPI_STYLES: Record<string, { bg: string; text: string; icon: string }> = {
  blue:   { bg: 'bg-blue-950/50', text: 'text-blue-400', icon: 'text-blue-500' },
  green:  { bg: 'bg-emerald-950/50', text: 'text-emerald-400', icon: 'text-emerald-500' },
  amber:  { bg: 'bg-amber-950/50', text: 'text-amber-400', icon: 'text-amber-500' },
  red:    { bg: 'bg-red-950/50', text: 'text-red-400', icon: 'text-red-500' },
  purple: { bg: 'bg-purple-950/50', text: 'text-purple-400', icon: 'text-purple-500' },
  cyan:   { bg: 'bg-cyan-950/50', text: 'text-cyan-400', icon: 'text-cyan-500' },
};
