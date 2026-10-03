import {
  ResponsiveContainer, BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, Cell, LabelList,
} from 'recharts';
import { CHART_COLORS } from '../../lib/colors';
import { metricColor } from '../../lib/colorScales';
import { tc } from '../../../../core/theme';

interface Props {
  data: Record<string, any>[];
  nameKey: string;
  valueKey: string;
  valueLabel: string;
  /** fixed bar colour, or colour by another field */
  color?: string;
  colorByKey?: string;
  colorInvert?: boolean;
  colorLo?: number;
  colorHi?: number;
  height?: number;
  showValueLabels?: boolean;
}

/** Horizontal ranking bar chart, optionally colour-encoded by a second metric. */
export default function HBarChart({
  data, nameKey, valueKey, valueLabel, color = tc('#3b82f6'),
  colorByKey, colorInvert = false, colorLo, colorHi,
  height, showValueLabels = true,
}: Props) {
  const h = height ?? Math.max(data.length * 34 + 30, 120);
  const cVals = colorByKey
    ? data.map(d => d[colorByKey]).filter((v: any) => v != null && isFinite(v)) : [];
  const lo = colorLo ?? (cVals.length ? Math.min(...cVals) : 0);
  const hi = colorHi ?? (cVals.length ? Math.max(...cVals) : 1);

  return (
    <ResponsiveContainer width="100%" height={h}>
      <BarChart data={data} layout="vertical" margin={{ left: 10, right: 40 }}>
        <CartesianGrid strokeDasharray="3 3" stroke={CHART_COLORS.grid} horizontal={false} />
        <XAxis type="number" tick={{ fill: tc('#9ca3af'), fontSize: 12 }} axisLine={false} tickLine={false} />
        <YAxis type="category" dataKey={nameKey} width={170}
          tick={{ fill: tc('#9ca3af'), fontSize: 12 }} axisLine={false} tickLine={false} />
        <Tooltip contentStyle={{ backgroundColor: CHART_COLORS.tooltipBg, border: `1px solid ${tc('#374151')}`, borderRadius: 8, color: tc('#f3f4f6') }} />
        <Bar dataKey={valueKey} name={valueLabel} radius={[0, 4, 4, 0]} fill={color}>
          {colorByKey && data.map((d, i) => (
            <Cell key={i} fill={metricColor(d[colorByKey], lo, hi, colorInvert)} />
          ))}
          {showValueLabels && (
            <LabelList dataKey={valueKey} position="right"
              style={{ fill: tc('#9ca3af'), fontSize: 12 }}
              formatter={(v: any) => (typeof v === 'number' ? (Math.abs(v) >= 100 ? v.toFixed(0) : v.toFixed(1)) : v)} />
          )}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}
