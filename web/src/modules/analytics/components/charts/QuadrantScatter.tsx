import {
  CartesianGrid, Cell, ReferenceLine, ResponsiveContainer, Scatter, ScatterChart,
  Tooltip, XAxis, YAxis, ZAxis,
} from 'recharts';
import { CHART_COLORS } from '../../lib/colors';
import { tc } from '../../../../core/theme';

export interface QuadrantPoint {
  name: string;
  x: number;
  y: number | null;
  z: number;
  quadrant: string;
  quadrantLabel?: string;
  /** confidence bounds on y, shown in the tooltip so a thin sample is visible */
  yLow?: number | null;
  yHigh?: number | null;
  detail?: string[];
}

export const QUADRANT_COLORS: Record<string, string> = {
  core: '#22c55e',
  critical: tc('#ef4444'),
  grow: '#38bdf8',
  review: tc('#9ca3af'),
};

interface Props {
  points: QuadrantPoint[];
  xSplit: number;
  ySplit: number;
  xLabel: string;
  yLabel: string;
  xSplitLabel?: string;
  ySplitLabel?: string;
  height?: number;
  onSelect?: (name: string) => void;
}

/**
 * A 2x2 decision grid: two reference lines cut the plane, and a point's colour
 * is its quadrant rather than a continuous scale.
 *
 * Deliberately not a gradient. The output of this chart is which of four
 * actions a carrier gets, and a continuous colour ramp invites the reader to
 * split hairs between two carriers that fall on the same side of both lines.
 */
export default function QuadrantScatter({
  points, xSplit, ySplit, xLabel, yLabel, xSplitLabel, ySplitLabel,
  height = 420, onSelect,
}: Props) {
  const data = points.filter(p => p.y != null) as (QuadrantPoint & { y: number })[];
  if (!data.length) {
    return <p className="text-gray-500 text-sm py-8">No carrier has a measurable on-time rate in this window.</p>;
  }

  return (
    <ResponsiveContainer width="100%" height={height}>
      <ScatterChart margin={{ top: 16, right: 24, bottom: 24, left: 10 }}>
        <CartesianGrid strokeDasharray="3 3" stroke={CHART_COLORS.grid} />
        <XAxis type="number" dataKey="x" name={xLabel} domain={['auto', 'auto']}
          tick={{ fill: tc('#9ca3af'), fontSize: 12 }} axisLine={false} tickLine={false}
          label={{ value: xLabel, position: 'insideBottom', offset: -14, fill: tc('#6b7280'), fontSize: 12 }} />
        <YAxis type="number" dataKey="y" name={yLabel} domain={[0, 100]}
          tick={{ fill: tc('#9ca3af'), fontSize: 12 }} axisLine={false} tickLine={false}
          label={{ value: yLabel, angle: -90, position: 'insideLeft', fill: tc('#6b7280'), fontSize: 12 }} />
        {/* Bubble area, not radius — recharts maps `range` to area, which is the
            perceptually honest encoding for a magnitude like freight share. */}
        <ZAxis type="number" dataKey="z" range={[60, 900]} name="share" />
        <Tooltip cursor={{ strokeDasharray: '3 3' }}
          content={({ payload }) => {
            const p = payload?.[0]?.payload as QuadrantPoint | undefined;
            if (!p) return null;
            return (
              <div className="bg-gray-900 border border-gray-700 rounded-lg px-3 py-2 text-xs text-gray-200 shadow-xl max-w-xs">
                <p className="font-semibold text-white mb-1">{p.name}</p>
                <p style={{ color: QUADRANT_COLORS[p.quadrant] }}>{p.quadrantLabel ?? p.quadrant}</p>
                <p>{xLabel}: {p.x}</p>
                <p>
                  {yLabel}: {p.y}
                  {p.yLow != null && p.yHigh != null && (
                    <span className="text-gray-500"> ({p.yLow}–{p.yHigh} at 95%)</span>
                  )}
                </p>
                {p.detail?.map((d, i) => <p key={i} className="text-gray-400">{d}</p>)}
              </div>
            );
          }} />
        <ReferenceLine x={xSplit} stroke={tc(tc('#6b7280'))} strokeDasharray="5 4"
          label={{ value: xSplitLabel ?? `x = ${xSplit}`, fill: tc('#9ca3af'), fontSize: 12, position: 'insideTopRight' }} />
        <ReferenceLine y={ySplit} stroke={tc(tc('#f59e0b'))} strokeDasharray="5 4"
          label={{ value: ySplitLabel ?? `y = ${ySplit}`, fill: tc('#f59e0b'), fontSize: 12, position: 'insideTopLeft' }} />
        <Scatter data={data} fillOpacity={0.8} isAnimationActive={false}
          onClick={(p: any) => onSelect?.(p?.name)}
          style={onSelect ? { cursor: 'pointer' } : undefined}>
          {data.map((d, i) => (
            <Cell key={i} fill={QUADRANT_COLORS[d.quadrant] ?? tc('#3b82f6')} />
          ))}
        </Scatter>
      </ScatterChart>
    </ResponsiveContainer>
  );
}
