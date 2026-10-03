import {
  ResponsiveContainer, RadarChart, PolarGrid, PolarAngleAxis, PolarRadiusAxis,
  Radar, Legend, Tooltip,
} from 'recharts';
import { CHART_COLORS } from '../../lib/colors';
import { tc } from '../../../../core/theme';

const PALETTE = [tc('#3b82f6'), tc('#10b981'), tc('#f59e0b'), tc('#ef4444')];

interface Props {
  /** rows: [{axis: 'OTD %', 'Carrier A': 80, 'Carrier B': 95}, …] all values 0–100 */
  data: Record<string, any>[];
  entities: string[];
  height?: number;
}

/** Head-to-head radar; values must be pre-normalised 0–100, bigger = better. */
export default function RadarCompareChart({ data, entities, height = 380 }: Props) {
  return (
    <ResponsiveContainer width="100%" height={height}>
      <RadarChart data={data} outerRadius="72%">
        <PolarGrid stroke={tc(tc('#374151'))} />
        <PolarAngleAxis dataKey="axis" tick={{ fill: tc('#9ca3af'), fontSize: 12 }} />
        <PolarRadiusAxis domain={[0, 100]} tick={{ fill: tc('#6b7280'), fontSize: 12 }} axisLine={false} />
        <Tooltip contentStyle={{ backgroundColor: CHART_COLORS.tooltipBg, border: `1px solid ${tc('#374151')}`, borderRadius: 8, color: tc('#f3f4f6') }} />
        <Legend wrapperStyle={{ fontSize: 12, color: tc('#9ca3af') }} />
        {entities.map((e, i) => (
          <Radar key={e} name={e} dataKey={e} stroke={PALETTE[i % PALETTE.length]}
            fill={PALETTE[i % PALETTE.length]} fillOpacity={0.15} strokeWidth={2} />
        ))}
      </RadarChart>
    </ResponsiveContainer>
  );
}
