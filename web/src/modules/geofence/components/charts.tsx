/**
 * Chart wrappers with the house styling, so every chart shares axes, grid,
 * tooltip and palette.
 */
import type { ReactNode } from 'react';
import {
  Bar, BarChart, CartesianGrid, Cell, Legend, Line, ComposedChart, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts';

import { PALETTE } from '../lib/theme';

// Re-exported so pages keep importing the palette from one place. It is the
// live theme palette: read it at render time, never copy it into a constant.
export { PALETTE };

// A function, not a constant: it must follow a theme switch.
const tooltipStyle = () => ({
  contentStyle: { background: PALETTE.tooltipBg, border: `1px solid ${PALETTE.tooltipBorder}`, borderRadius: 8, fontSize: 12 },
  labelStyle: { color: PALETTE.tooltipText, marginBottom: 4 },
  itemStyle: { padding: 0 },
  cursor: { fill: PALETTE.cursor },
});

export interface Series {
  key: string;
  label: string;
  color: string;
  type?: 'bar' | 'line';
  stack?: string;
  axis?: 'left' | 'right';
}

export function SeriesChart({ data, x, series, height = 240, xFormat, yFormat, rightFormat, onClickX, highlightX }: {
  data: any[]; x: string; series: Series[]; height?: number;
  xFormat?: (v: any) => string; yFormat?: (v: any) => string; rightFormat?: (v: any) => string;
  onClickX?: (v: any) => void; highlightX?: any;
}) {
  const hasRight = series.some(s => s.axis === 'right');
  return (
    <ResponsiveContainer width="100%" height={height}>
      <ComposedChart data={data} margin={{ top: 8, right: hasRight ? 0 : 8, left: -8, bottom: 0 }}
        onClick={(e: any) => { if (onClickX && e?.activeLabel !== undefined) onClickX(e.activeLabel); }}>
        <CartesianGrid stroke={PALETTE.grid} vertical={false} />
        <XAxis dataKey={x} tick={{ fill: PALETTE.axis, fontSize: 12 }} tickFormatter={xFormat}
          axisLine={{ stroke: PALETTE.grid }} tickLine={false} minTickGap={12} />
        <YAxis yAxisId="left" tick={{ fill: PALETTE.axis, fontSize: 12 }} tickFormatter={yFormat}
          axisLine={false} tickLine={false} width={48} />
        {hasRight && (
          <YAxis yAxisId="right" orientation="right" tick={{ fill: PALETTE.axis, fontSize: 12 }}
            tickFormatter={rightFormat} axisLine={false} tickLine={false} width={44} />
        )}
        <Tooltip {...tooltipStyle()} labelFormatter={xFormat as any}
          formatter={(v: any, name: any) => {
            const s = series.find(q => q.label === name);
            const f = s?.axis === 'right' ? rightFormat : yFormat;
            return [f ? f(v) : v, name];
          }} />
        <Legend wrapperStyle={{ fontSize: 12, color: PALETTE.legend }} iconSize={8} />
        {series.map(s => s.type === 'line' ? (
          <Line key={s.key} yAxisId={s.axis || 'left'} dataKey={s.key} name={s.label} stroke={s.color}
            strokeWidth={2} dot={false} isAnimationActive={false} />
        ) : (
          <Bar key={s.key} yAxisId={s.axis || 'left'} dataKey={s.key} name={s.label} fill={s.color}
            stackId={s.stack} radius={s.stack ? 0 : [3, 3, 0, 0]} isAnimationActive={false}
            cursor={onClickX ? 'pointer' : undefined}>
            {highlightX !== undefined && data.map((d, i) => (
              <Cell key={i} fillOpacity={d[x] === highlightX ? 1 : 0.45} />
            ))}
          </Bar>
        ))}
      </ComposedChart>
    </ResponsiveContainer>
  );
}

export function HBars({ data, label, value, color, height, format, onClick }: {
  data: any[]; label: string; value: string; color?: string; height?: number;
  format?: (v: any) => string; onClick?: (row: any) => void;
}) {
  const h = height ?? Math.max(120, data.length * 26 + 20);
  return (
    <ResponsiveContainer width="100%" height={h}>
      <BarChart data={data} layout="vertical" margin={{ top: 0, right: 16, left: 8, bottom: 0 }}>
        <CartesianGrid stroke={PALETTE.grid} horizontal={false} />
        <XAxis type="number" tick={{ fill: PALETTE.axis, fontSize: 12 }} tickFormatter={format} axisLine={false} tickLine={false} />
        <YAxis type="category" dataKey={label} width={170} tick={{ fill: PALETTE.categoryTick, fontSize: 12 }}
          tickFormatter={(v: string) => (v?.length > 26 ? `${v.slice(0, 25)}…` : v)} axisLine={false} tickLine={false} />
        <Tooltip {...tooltipStyle()} formatter={(v: any) => [format ? format(v) : v, '']} />
        <Bar dataKey={value} fill={color ?? PALETTE.blue} radius={[0, 3, 3, 0]} isAnimationActive={false}
          cursor={onClick ? 'pointer' : undefined} onClick={(d: any) => onClick?.(d?.payload ?? d)} />
      </BarChart>
    </ResponsiveContainer>
  );
}

/** Weekday x hour matrix, as a grid of shaded cells. */
export function WeekHourHeatmap({ matrix, unit = 'arrivals' }: { matrix: number[][]; unit?: string }) {
  const days = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
  const max = Math.max(1, ...matrix.flat());
  return (
    <div className="overflow-x-auto">
      <div className="inline-grid gap-[3px]" style={{ gridTemplateColumns: `36px repeat(24, minmax(18px, 1fr))` }}>
        <div />
        {Array.from({ length: 24 }, (_, h) => (
          <div key={h} className="text-xs text-gray-600 text-center">{h % 3 === 0 ? h : ''}</div>
        ))}
        {matrix.map((row, d) => (
          <Row key={d} label={days[d]}>
            {row.map((v, h) => (
              <div key={h} title={`${days[d]} ${String(h).padStart(2, '0')}:00 — ${v} ${unit}`}
                className="h-[18px] rounded-[3px]"
                style={{ background: v ? `rgba(${PALETTE.heat.join(',')},${0.12 + 0.88 * (v / max)})` : PALETTE.heatEmpty }} />
            ))}
          </Row>
        ))}
      </div>
    </div>
  );
}

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <>
      <div className="text-xs text-gray-500 flex items-center">{label}</div>
      {children}
    </>
  );
}

/** A tiny inline bar list for "top N" cards. */
export function BarList({ rows, max, render }: {
  rows: { key: string | number; label: ReactNode; value: number; sub?: ReactNode; onClick?: () => void }[];
  max?: number; render?: (v: number) => string;
}) {
  const top = max ?? Math.max(1, ...rows.map(r => r.value));
  return (
    <div className="space-y-1.5">
      {rows.map(r => (
        <div key={r.key} onClick={r.onClick}
          className={`relative rounded-md overflow-hidden ${r.onClick ? 'cursor-pointer hover:bg-gray-800/60' : ''}`}>
          <div className="absolute inset-y-0 left-0 bg-blue-600/15" style={{ width: `${(100 * r.value) / top}%` }} />
          <div className="relative flex items-center justify-between gap-3 px-2.5 py-1.5 text-sm">
            <span className="text-gray-200 truncate min-w-0">{r.label}</span>
            <span className="text-gray-400 tabular shrink-0 text-xs">
              {render ? render(r.value) : r.value.toLocaleString('en-IN')}{r.sub && <span className="ml-2 text-gray-600">{r.sub}</span>}
            </span>
          </div>
        </div>
      ))}
    </div>
  );
}
