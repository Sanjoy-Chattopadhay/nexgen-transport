import { ResponsiveContainer, BarChart as RechartsBar, Bar, XAxis, YAxis, CartesianGrid, Tooltip, Legend } from 'recharts';
import { CHART_COLORS } from '../../lib/colors';
import { tc } from '../../../../core/theme';

interface Series { key: string; color: string; label: string; }
interface Props {
  data: any[];
  xKey: string;
  series: Series[];
  height?: number;
  /** stack the series into one bar per x value instead of side-by-side */
  stacked?: boolean;
  showLegend?: boolean;
  /** angle long category labels so they stay readable */
  angledLabels?: boolean;
}

export default function BarChart({
  data, xKey, series, height = 300, stacked = false, showLegend = false, angledLabels = false,
}: Props) {
  return (
    <ResponsiveContainer width="100%" height={height}>
      <RechartsBar data={data} margin={angledLabels ? { bottom: 60 } : undefined}>
        <CartesianGrid strokeDasharray="3 3" stroke={CHART_COLORS.grid} />
        {/* Every label while they fit; on long series (30-minute windows across
            a multi-day trip) Recharts drops the ones that would overlap. */}
        <XAxis dataKey={xKey} tick={{ fill: tc('#9ca3af'), fontSize: 12 }}
          axisLine={false} tickLine={false} minTickGap={8}
          interval={data.length > 24 ? 'preserveStartEnd' : 0} angle={angledLabels ? -35 : 0} textAnchor={angledLabels ? 'end' : 'middle'}
          height={angledLabels ? 80 : undefined}
          tickFormatter={angledLabels ? (v: string) => (v.length > 18 ? `${v.slice(0, 18)}…` : v) : undefined} />
        <YAxis tick={{ fill: tc('#9ca3af'), fontSize: 12 }} axisLine={false} tickLine={false} />
        <Tooltip contentStyle={{ backgroundColor: CHART_COLORS.tooltipBg, border: `1px solid ${tc('#374151')}`, borderRadius: 8, color: tc('#f3f4f6') }} />
        {showLegend && <Legend wrapperStyle={{ fontSize: 12, color: tc('#9ca3af') }} />}
        {series.map((s, i) => (
          <Bar key={s.key} dataKey={s.key} name={s.label} fill={s.color}
            stackId={stacked ? 'a' : undefined}
            // only the top segment of a stack gets rounded corners
            radius={stacked ? (i === series.length - 1 ? [4, 4, 0, 0] : [0, 0, 0, 0]) : [4, 4, 0, 0]} />
        ))}
      </RechartsBar>
    </ResponsiveContainer>
  );
}
