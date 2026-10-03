import {
  ResponsiveContainer, LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ReferenceLine,
} from 'recharts';
import { CHART_COLORS } from '../../lib/colors';
import { tc } from '../../../../core/theme';

interface Props {
  points: { value: number; pct: number }[];
  p95?: number | null;
  unit?: string;
  height?: number;
}

/** Cumulative distribution — read the value where the curve crosses 95%. */
export default function ECDFChart({ points, p95, unit = '', height = 300 }: Props) {
  return (
    <ResponsiveContainer width="100%" height={height}>
      <LineChart data={points}>
        <CartesianGrid strokeDasharray="3 3" stroke={CHART_COLORS.grid} />
        <XAxis dataKey="value" type="number" domain={['auto', 'auto']}
          tick={{ fill: tc('#9ca3af'), fontSize: 12 }} axisLine={false} tickLine={false} />
        <YAxis domain={[0, 100]} tick={{ fill: tc('#9ca3af'), fontSize: 12 }} axisLine={false} tickLine={false}
          tickFormatter={(v: number) => `${v}%`} />
        <Tooltip contentStyle={{ backgroundColor: CHART_COLORS.tooltipBg, border: `1px solid ${tc('#374151')}`, borderRadius: 8, color: tc('#f3f4f6') }}
          formatter={(v: any) => [`${v}%`, '% of trips ≤ value']} labelFormatter={(l) => `${l}${unit}`} />
        <ReferenceLine y={95} stroke={tc(tc('#ef4444'))} strokeDasharray="4 4"
          label={{ value: '95%', fill: tc('#ef4444'), fontSize: 12, position: 'insideLeft' }} />
        {p95 != null && (
          <ReferenceLine x={p95} stroke={tc(tc('#ef4444'))} strokeDasharray="2 4"
            label={{ value: `P95 = ${p95}${unit}`, fill: tc('#f87171'), fontSize: 12, position: 'insideTopLeft' }} />
        )}
        <Line type="monotone" dataKey="pct" stroke={tc(tc('#06b6d4'))} strokeWidth={2} dot={false} />
      </LineChart>
    </ResponsiveContainer>
  );
}
