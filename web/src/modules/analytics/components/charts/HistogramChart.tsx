import {
  ResponsiveContainer, BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ReferenceLine,
} from 'recharts';
import { CHART_COLORS } from '../../lib/colors';
import { tc } from '../../../../core/theme';

interface Bin { bin_start: number; bin_end: number; mid: number; count: number; }

interface Props {
  bins: Bin[];
  mean?: number | null;
  median?: number | null;
  height?: number;
  color?: string;
}

/** Histogram from backend-computed bins, with mean / median reference lines. */
export default function HistogramChart({ bins, mean, median, height = 300, color = tc('#3b82f6') }: Props) {
  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart data={bins} barCategoryGap={1}>
        <CartesianGrid strokeDasharray="3 3" stroke={CHART_COLORS.grid} />
        <XAxis dataKey="mid" tick={{ fill: tc('#9ca3af'), fontSize: 12 }} axisLine={false} tickLine={false}
          tickFormatter={(v: number) => Number(v).toFixed(0)} />
        <YAxis tick={{ fill: tc('#9ca3af'), fontSize: 12 }} axisLine={false} tickLine={false} />
        <Tooltip contentStyle={{ backgroundColor: CHART_COLORS.tooltipBg, border: `1px solid ${tc('#374151')}`, borderRadius: 8, color: tc('#f3f4f6') }}
          labelFormatter={(_, pl) => {
            const b = pl?.[0]?.payload as Bin | undefined;
            return b ? `${b.bin_start} – ${b.bin_end}` : '';
          }} />
        {mean != null && (
          <ReferenceLine x={mean} stroke="#eab308" strokeDasharray="6 4"
            label={{ value: `mean ${mean}`, fill: '#eab308', fontSize: 12, position: 'top' }} />
        )}
        {median != null && (
          <ReferenceLine x={median} stroke="#22c55e" strokeDasharray="2 4"
            label={{ value: `median ${median}`, fill: '#22c55e', fontSize: 12, position: 'insideTopLeft' }} />
        )}
        <Bar dataKey="count" fill={color} radius={[2, 2, 0, 0]} />
      </BarChart>
    </ResponsiveContainer>
  );
}
