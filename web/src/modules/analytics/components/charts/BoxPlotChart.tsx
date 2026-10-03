import type { BoxGroup } from '../../services/ttaDashboard';
import { tc } from '../../../../core/theme';

interface Props {
  groups: BoxGroup[];
  unit?: string;
  height?: number;
}

/** Horizontal box-and-whisker plot rendered as SVG from backend quartile stats. */
export default function BoxPlotChart({ groups, unit = 'h', height }: Props) {
  if (!groups.length) return <p className="text-gray-500 text-sm py-6">No data for this selection</p>;

  const rowH = 34, labelW = 190, padR = 24, W = 760;
  const H = height ?? groups.length * rowH + 30;
  const plotW = W - labelW - padR;

  const lo = Math.min(...groups.map(g => Math.min(g.whisker_lo, ...(g.outliers.length ? g.outliers : [g.whisker_lo]))));
  const hi = Math.max(...groups.map(g => Math.max(g.whisker_hi, ...(g.outliers.length ? g.outliers : [g.whisker_hi]))));
  const span = hi - lo || 1;
  const X = (v: number) => labelW + ((v - lo) / span) * plotW;

  // axis ticks
  const ticks = Array.from({ length: 5 }, (_, i) => lo + (span * i) / 4);

  return (
    <div className="overflow-x-auto">
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full" style={{ minWidth: 560 }}>
        {ticks.map((t, i) => (
          <g key={i}>
            <line x1={X(t)} y1={4} x2={X(t)} y2={H - 22} stroke={tc(tc('#1f2937'))} strokeDasharray="3 3" />
            <text x={X(t)} y={H - 8} textAnchor="middle" fill={tc(tc('#6b7280'))} fontSize={12}>
              {t.toFixed(span < 10 ? 1 : 0)}{unit}
            </text>
          </g>
        ))}
        {groups.map((g, i) => {
          const cy = i * rowH + 20;
          return (
            <g key={g.group}>
              <text x={labelW - 8} y={cy + 4} textAnchor="end" fill={tc(tc('#9ca3af'))} fontSize={12}>
                {g.group.length > 26 ? g.group.slice(0, 26) + '…' : g.group}
              </text>
              {/* whiskers */}
              <line x1={X(g.whisker_lo)} y1={cy} x2={X(g.q1)} y2={cy} stroke="#64748b" strokeWidth={1.5} />
              <line x1={X(g.q3)} y1={cy} x2={X(g.whisker_hi)} y2={cy} stroke="#64748b" strokeWidth={1.5} />
              <line x1={X(g.whisker_lo)} y1={cy - 6} x2={X(g.whisker_lo)} y2={cy + 6} stroke="#64748b" strokeWidth={1.5} />
              <line x1={X(g.whisker_hi)} y1={cy - 6} x2={X(g.whisker_hi)} y2={cy + 6} stroke="#64748b" strokeWidth={1.5} />
              {/* box */}
              <rect x={X(g.q1)} y={cy - 9} width={Math.max(X(g.q3) - X(g.q1), 2)} height={18}
                fill={tc(tc('#3b82f6'))} fillOpacity={0.35} stroke={tc(tc('#3b82f6'))} rx={2}>
                <title>{`${g.group}\nn=${g.count}  median ${g.median}${unit}\nQ1 ${g.q1} · Q3 ${g.q3}\nwhiskers ${g.whisker_lo}–${g.whisker_hi}`}</title>
              </rect>
              {/* median + mean */}
              <line x1={X(g.median)} y1={cy - 9} x2={X(g.median)} y2={cy + 9} stroke={tc(tc('#f3f4f6'))} strokeWidth={2} />
              <circle cx={X(g.mean)} cy={cy} r={2.5} fill={tc(tc('#f59e0b'))} />
              {/* outliers */}
              {g.outliers.map((o, j) => (
                <circle key={j} cx={X(o)} cy={cy} r={2} fill={tc(tc('#ef4444'))} fillOpacity={0.8}>
                  <title>{`outlier ${o}${unit}`}</title>
                </circle>
              ))}
            </g>
          );
        })}
      </svg>
      <p className="text-xs text-gray-600 mt-1">
        box = middle 50% · white line = median · <span className="text-amber-500">●</span> mean · <span className="text-red-500">●</span> outliers beyond 1.5×IQR
      </p>
    </div>
  );
}
