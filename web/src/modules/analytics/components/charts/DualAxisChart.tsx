import {
  ResponsiveContainer, ComposedChart, Line, Bar, Area, XAxis, YAxis,
  CartesianGrid, Tooltip, Legend, ReferenceLine,
} from 'recharts';
import { CHART_COLORS } from '../../lib/colors';
import { tc } from '../../../../core/theme';

export interface DualSeries {
  key: string;
  label: string;
  color: string;
  axis?: 'left' | 'right';
  type?: 'line' | 'bar' | 'area';
  dashed?: boolean;
}

interface Props {
  data: any[];
  xKey: string;
  series: DualSeries[];
  height?: number;
  rightDomain?: [number, number];
  refLineRight?: { y: number; label?: string };
  refLineLeft?: { y: number; label?: string };
}

/** Composed chart with optional secondary Y axis, mixing bars/lines/areas. */
export default function DualAxisChart({ data, xKey, series, height = 300, rightDomain, refLineRight, refLineLeft }: Props) {
  const hasRight = series.some(s => s.axis === 'right');
  return (
    <ResponsiveContainer width="100%" height={height}>
      <ComposedChart data={data}>
        <CartesianGrid strokeDasharray="3 3" stroke={CHART_COLORS.grid} />
        <XAxis dataKey={xKey} tick={{ fill: tc('#9ca3af'), fontSize: 12 }} axisLine={false} tickLine={false} />
        <YAxis yAxisId="left" tick={{ fill: tc('#9ca3af'), fontSize: 12 }} axisLine={false} tickLine={false} />
        {hasRight && (
          <YAxis yAxisId="right" orientation="right" domain={rightDomain}
            tick={{ fill: tc('#9ca3af'), fontSize: 12 }} axisLine={false} tickLine={false} />
        )}
        <Tooltip contentStyle={{ backgroundColor: CHART_COLORS.tooltipBg, border: `1px solid ${tc('#374151')}`, borderRadius: 8, color: tc('#f3f4f6') }} />
        <Legend wrapperStyle={{ fontSize: 12, color: tc('#9ca3af') }} />
        {refLineLeft && (
          <ReferenceLine yAxisId="left" y={refLineLeft.y} stroke={tc(tc('#ef4444'))} strokeDasharray="4 4"
            label={{ value: refLineLeft.label, fill: tc('#ef4444'), fontSize: 12, position: 'insideTopRight' }} />
        )}
        {refLineRight && hasRight && (
          <ReferenceLine yAxisId="right" y={refLineRight.y} stroke={tc(tc('#ef4444'))} strokeDasharray="4 4"
            label={{ value: refLineRight.label, fill: tc('#ef4444'), fontSize: 12, position: 'insideTopRight' }} />
        )}
        {series.map(s => {
          const common = { yAxisId: s.axis ?? 'left', dataKey: s.key, name: s.label } as any;
          if (s.type === 'bar') return <Bar key={s.key} {...common} fill={s.color} radius={[3, 3, 0, 0]} />;
          if (s.type === 'area') return (
            <Area key={s.key} {...common} type="monotone" stroke={s.color} fill={s.color} fillOpacity={0.15} strokeWidth={2} dot={false} />
          );
          return (
            <Line key={s.key} {...common} type="monotone" stroke={s.color} strokeWidth={2}
              strokeDasharray={s.dashed ? '6 4' : undefined} dot={false} />
          );
        })}
      </ComposedChart>
    </ResponsiveContainer>
  );
}
