import { ResponsiveContainer, PieChart, Pie, Cell, Tooltip, Legend } from 'recharts';
import { CHART_COLORS } from '../../lib/colors';
import { tc } from '../../../../core/theme';

const PALETTE = [tc('#3b82f6'), tc('#10b981'), tc('#f59e0b'), tc('#ef4444'), '#8b5cf6',
  tc('#06b6d4'), '#f472b6', '#84cc16', '#fb923c', '#a78bfa'];

interface Props {
  data: { name: string; value: number; color?: string }[];
  height?: number;
  innerRadius?: number;
  showLegend?: boolean;
}

export default function DonutChart({ data, height = 280, innerRadius = 60, showLegend = true }: Props) {
  return (
    <ResponsiveContainer width="100%" height={height}>
      <PieChart>
        <Pie data={data} dataKey="value" nameKey="name" innerRadius={innerRadius} outerRadius={innerRadius + 40}
          paddingAngle={2} stroke={tc(tc('#111827'))}>
          {data.map((d, i) => (
            <Cell key={d.name} fill={d.color ?? PALETTE[i % PALETTE.length]} />
          ))}
        </Pie>
        <Tooltip contentStyle={{ backgroundColor: CHART_COLORS.tooltipBg, border: `1px solid ${tc('#374151')}`, borderRadius: 8, color: tc('#f3f4f6') }} />
        {showLegend && <Legend wrapperStyle={{ fontSize: 12, color: tc('#9ca3af') }} />}
      </PieChart>
    </ResponsiveContainer>
  );
}
