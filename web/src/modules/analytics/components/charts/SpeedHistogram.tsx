import {
  Bar, BarChart, Cell, CartesianGrid, ReferenceLine, ResponsiveContainer,
  Tooltip, XAxis, YAxis,
} from 'recharts';
import { CHART_COLORS } from '../../lib/colors';
import { tc } from '../../../../core/theme';

export interface SpeedBin {
  kmph: number;
  pings: number;
  minutes: number | null;
  over: boolean;
}

interface Props {
  bins: SpeedBin[];
  limit: number;
  height?: number;
  /** Drop the parked pings. Speed 0 is usually 60-70% of every trail, and left
   *  in it flattens the entire driving distribution against the axis. */
  hideStopped?: boolean;
  color?: string;
  overColor?: string;
}

/**
 * Time spent at each km/h, with everything above the limit coloured as a
 * breach.
 *
 * Weighted by MINUTES rather than by ping count: trackers ping at different
 * rates, and a dense tracker would otherwise look like a truck that spent
 * longer at that speed. Minutes are what a speed limit is actually breached
 * for.
 */
export default function SpeedHistogram({
  bins, limit, height = 280, hideStopped = true,
  color = '#38bdf8', overColor = tc('#ef4444'),
}: Props) {
  const data = (hideStopped ? bins.filter(b => b.kmph > 0) : bins)
    .map(b => ({ ...b, minutes: b.minutes ?? 0 }));

  if (!data.length) {
    return <p className="text-gray-500 text-sm py-8">No movement recorded in this zone.</p>;
  }

  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart data={data} barCategoryGap={0}>
        <CartesianGrid strokeDasharray="3 3" stroke={CHART_COLORS.grid} />
        <XAxis dataKey="kmph" tick={{ fill: tc('#9ca3af'), fontSize: 12 }}
          axisLine={false} tickLine={false} interval={4}
          label={{ value: 'km/h', position: 'insideBottomRight', offset: -4, fill: tc('#6b7280'), fontSize: 12 }} />
        <YAxis tick={{ fill: tc('#9ca3af'), fontSize: 12 }} axisLine={false} tickLine={false}
          label={{ value: 'minutes', angle: -90, position: 'insideLeft', fill: tc('#6b7280'), fontSize: 12 }} />
        <Tooltip
          contentStyle={{ backgroundColor: CHART_COLORS.tooltipBg, border: `1px solid ${tc('#374151')}`, borderRadius: 8, color: tc('#f3f4f6') }}
          formatter={((v: number | undefined, _n: unknown, p: any) => [
            `${Number(v ?? 0).toFixed(0)} min · ${p?.payload?.pings ?? 0} pings`,
            p?.payload?.over ? 'Over the limit' : 'Within the limit',
          ]) as never}
          labelFormatter={(v) => `${v} km/h`} />
        <ReferenceLine x={limit} stroke={tc(tc('#f59e0b'))} strokeDasharray="5 3"
          label={{ value: `limit ${limit}`, fill: tc('#f59e0b'), fontSize: 12, position: 'top' }} />
        {/* Animation off: these panels sit in tabs that mount hidden, and an
            animated recharts series that mounts at zero width never draws. */}
        <Bar dataKey="minutes" isAnimationActive={false}>
          {data.map(b => (
            <Cell key={b.kmph} fill={b.over ? overColor : color} />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}
