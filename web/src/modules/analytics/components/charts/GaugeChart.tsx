import { tc } from '../../../../core/theme';
interface Props {
  value: number | null | undefined;
  target: number;
  min?: number;
  max?: number;
  label?: string;
  height?: number;
}

/** Semi-circular gauge with red / amber / green zones vs a target line. */
export default function GaugeChart({ value, target, min = 0, max = 100, label = '', height = 220 }: Props) {
  const W = 320, H = 190, CX = W / 2, CY = 165, R = 130, THICK = 26;

  const frac = (v: number) => Math.max(0, Math.min(1, (v - min) / (max - min)));
  const angle = (v: number) => Math.PI - Math.PI * frac(v); // pi..0

  const arcPath = (from: number, to: number) => {
    const a0 = angle(from), a1 = angle(to);
    const x0 = CX + R * Math.cos(a0), y0 = CY - R * Math.sin(a0);
    const x1 = CX + R * Math.cos(a1), y1 = CY - R * Math.sin(a1);
    const large = Math.abs(a0 - a1) > Math.PI ? 1 : 0;
    return `M ${x0} ${y0} A ${R} ${R} 0 ${large} 1 ${x1} ${y1}`;
  };

  const zones: { from: number; to: number; color: string }[] = [
    { from: min, to: 0.85 * target, color: tc('#7f1d1d') },
    { from: 0.85 * target, to: target, color: tc('#78350f') },
    { from: target, to: max, color: '#14532d' },
  ];

  const v = value ?? min;
  const na = angle(v);
  const nx = CX + (R - THICK / 2 - 8) * Math.cos(na);
  const ny = CY - (R - THICK / 2 - 8) * Math.sin(na);
  const ta = angle(target);
  const tx0 = CX + (R - THICK - 6) * Math.cos(ta), ty0 = CY - (R - THICK - 6) * Math.sin(ta);
  const tx1 = CX + (R + 6) * Math.cos(ta), ty1 = CY - (R + 6) * Math.sin(ta);

  const ok = value != null && value >= target;

  return (
    <div className="flex flex-col items-center justify-center" style={{ height }}>
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full max-w-[340px]">
        {zones.map((z, i) => (
          <path key={i} d={arcPath(z.from, z.to)} stroke={z.color} strokeWidth={THICK} fill="none" />
        ))}
        {value != null && (
          <path d={arcPath(min, v)} stroke={ok ? '#22c55e' : v >= 0.85 * target ? tc('#f59e0b') : tc('#ef4444')}
            strokeWidth={THICK - 14} fill="none" strokeLinecap="round" />
        )}
        <line x1={tx0} y1={ty0} x2={tx1} y2={ty1} stroke={tc(tc('#ef4444'))} strokeWidth={3} />
        {value != null && (
          <>
            <line x1={CX} y1={CY} x2={nx} y2={ny} stroke={tc(tc('#e5e7eb'))} strokeWidth={2.5} />
            <circle cx={CX} cy={CY} r={6} fill={tc(tc('#e5e7eb'))} />
          </>
        )}
        <text x={CX} y={CY - 34} textAnchor="middle" fill={ok ? '#4ade80' : tc('#f87171')}
          fontSize={34} fontWeight={700}>
          {value != null ? value.toFixed(1) : '—'}
        </text>
        <text x={CX} y={CY - 12} textAnchor="middle" fill={tc(tc('#9ca3af'))} fontSize={12}>
          {label} · target {target}
        </text>
      </svg>
    </div>
  );
}
