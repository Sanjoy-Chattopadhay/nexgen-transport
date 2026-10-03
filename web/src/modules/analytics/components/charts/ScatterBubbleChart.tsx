import {
  ResponsiveContainer, ScatterChart, Scatter, XAxis, YAxis, ZAxis,
  CartesianGrid, Tooltip, Cell, ReferenceLine,
} from 'recharts';
import { CHART_COLORS } from '../../lib/colors';
import { metricColor } from '../../lib/colorScales';
import { tc } from '../../../../core/theme';

export interface BubblePoint {
  name: string;
  x: number;
  y: number;
  z: number;      // bubble size driver
  c?: number;     // color driver
  [key: string]: any;
}

interface Props {
  data: BubblePoint[];
  xLabel: string;
  yLabel: string;
  zLabel?: string;
  cLabel?: string;
  invertColor?: boolean;
  height?: number;
  yDomain?: [number | 'auto', number | 'auto'];
  trendLine?: { slope: number; intercept: number } | null;
  extraTooltip?: (p: BubblePoint) => string[];
}

/** 4-dimensional bubble scatter: x, y, size (z) and red→green color (c). */
export default function ScatterBubbleChart({
  data, xLabel, yLabel, zLabel, cLabel, invertColor = false, height = 340,
  yDomain, trendLine, extraTooltip,
}: Props) {
  const cVals = data.map(d => d.c).filter((v): v is number => v != null && isFinite(v));
  const cLo = cVals.length ? Math.min(...cVals) : 0;
  const cHi = cVals.length ? Math.max(...cVals) : 1;

  const xs = data.map(d => d.x);
  const xMin = xs.length ? Math.min(...xs) : 0;
  const xMax = xs.length ? Math.max(...xs) : 1;

  return (
    <ResponsiveContainer width="100%" height={height}>
      <ScatterChart margin={{ top: 10, right: 20, bottom: 20, left: 10 }}>
        <CartesianGrid strokeDasharray="3 3" stroke={CHART_COLORS.grid} />
        <XAxis type="number" dataKey="x" name={xLabel} domain={['auto', 'auto']}
          tick={{ fill: tc('#9ca3af'), fontSize: 12 }} axisLine={false} tickLine={false}
          label={{ value: xLabel, position: 'insideBottom', offset: -12, fill: tc('#6b7280'), fontSize: 12 }} />
        <YAxis type="number" dataKey="y" name={yLabel} domain={yDomain ?? ['auto', 'auto']}
          tick={{ fill: tc('#9ca3af'), fontSize: 12 }} axisLine={false} tickLine={false}
          label={{ value: yLabel, angle: -90, position: 'insideLeft', fill: tc('#6b7280'), fontSize: 12 }} />
        <ZAxis type="number" dataKey="z" range={[60, 900]} name={zLabel} />
        <Tooltip cursor={{ strokeDasharray: '3 3' }}
          content={({ payload }) => {
            const p = payload?.[0]?.payload as BubblePoint | undefined;
            if (!p) return null;
            return (
              <div className="bg-gray-900 border border-gray-700 rounded-lg px-3 py-2 text-xs text-gray-200 shadow-xl">
                <p className="font-semibold text-white mb-1">{p.name}</p>
                <p>{xLabel}: {p.x?.toFixed?.(1)}</p>
                <p>{yLabel}: {p.y?.toFixed?.(1)}</p>
                {zLabel && <p>{zLabel}: {p.z?.toLocaleString?.('en-IN')}</p>}
                {cLabel && p.c != null && <p>{cLabel}: {p.c?.toFixed?.(1)}</p>}
                {extraTooltip?.(p).map((l, i) => <p key={i}>{l}</p>)}
              </div>
            );
          }} />
        {trendLine && isFinite(trendLine.slope) && (
          <ReferenceLine stroke="#eab308" strokeDasharray="6 4" ifOverflow="extendDomain"
            segment={[
              { x: xMin, y: trendLine.intercept + trendLine.slope * xMin },
              { x: xMax, y: trendLine.intercept + trendLine.slope * xMax },
            ]} />
        )}
        <Scatter data={data} fillOpacity={0.75}>
          {data.map((d, i) => (
            <Cell key={i} fill={d.c != null ? metricColor(d.c, cLo, cHi, invertColor) : tc('#3b82f6')} />
          ))}
        </Scatter>
      </ScatterChart>
    </ResponsiveContainer>
  );
}
