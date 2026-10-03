/**
 * A trip as a number line.
 *
 * The whole trip, first GPS fix to last, laid out left to right like a
 * ruler: the phases on top (before loading, loading, transit, unloading,
 * after), what filled them in the bar (driving, stops, halts at intermediate
 * places, stretches the GPS was silent), and two scales -- the clock along
 * the bottom and the kilometre milestones along the top, placed where the
 * truck actually passed them. Switch to the distance scale and the same trip
 * reads by kilometre instead: stays collapse to the point where they happened
 * and are drawn as pins, taller for longer.
 *
 * A playhead is shared with the 3D space-time view: drag along the line and
 * the truck moves there too.
 */
import { useEffect, useMemo, useRef, useState } from 'react';
import { segmentColor, segmentOpacity, PHASE_LABEL, KIND_TEXT, LEGEND } from './phaseColors';
import { fmtDateTime, fmtDuration, fmtKm } from '../../lib/format';
import { PALETTE } from '../../lib/theme';

export interface Segment {
  seq: number; phase: string; kind: string; start: string; end: string; duration_s: number;
  km_from?: number; km_to?: number; name?: string; open?: boolean; straight_km?: number | null;
}

const ms = (s: string) => new Date(s.replace(' ', 'T')).getTime();

function niceStep(span: number, target: number, steps: number[]): number {
  const raw = span / Math.max(1, target);
  return steps.find(s => s >= raw) ?? steps[steps.length - 1];
}

const TIME_STEPS = [15, 30, 60, 120, 180, 360, 720, 1440, 2880, 4320, 10080].map(m => m * 60_000);
const KM_STEPS = [0.5, 1, 2, 5, 10, 20, 25, 50, 100, 200, 250, 500, 1000];

function clock(t: number, withDay: boolean): string {
  const d = new Date(t);
  const hm = d.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', hour12: false });
  return withDay ? `${d.toLocaleDateString('en-GB', { day: '2-digit', month: 'short' })} ${hm}` : hm;
}

export default function NumberLine({ segments, cursor, onCursor, onSelect, selected }: {
  segments: Segment[];
  cursor?: number | null;
  onCursor?: (t: number) => void;
  onSelect?: (s: Segment) => void;
  selected?: number | null;
}) {
  const wrap = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(900);
  const [scale, setScale] = useState<'time' | 'distance'>('time');
  const [hover, setHover] = useState<{ x: number; seg: Segment } | null>(null);
  const dragging = useRef(false);

  useEffect(() => {
    if (!wrap.current) return;
    const ro = new ResizeObserver(e => setWidth(Math.max(320, e[0].contentRect.width)));
    ro.observe(wrap.current);
    return () => ro.disconnect();
  }, []);

  const segs = useMemo(() => segments.map(s => ({ ...s, t0: ms(s.start), t1: ms(s.end) })), [segments]);
  const T0 = segs.length ? segs[0].t0 : 0;
  const T1 = segs.length ? segs[segs.length - 1].t1 : 1;
  const K1 = segs.length ? Math.max(0.01, ...segs.map(s => s.km_to ?? 0)) : 1;
  const hasKm = segs.some(s => s.km_to != null);
  const pad = 14;
  const W = width - pad * 2;

  // time → km and back, piecewise linear through the segment boundaries
  const kmAt = (t: number) => {
    const s = segs.find(x => t >= x.t0 && t <= x.t1) ?? (t < T0 ? segs[0] : segs[segs.length - 1]);
    if (!s || s.km_from == null || s.km_to == null) return 0;
    const f = s.t1 > s.t0 ? (Math.min(Math.max(t, s.t0), s.t1) - s.t0) / (s.t1 - s.t0) : 0;
    return s.km_from + f * (s.km_to - s.km_from);
  };
  const timeAtKm = (k: number) => {
    const s = segs.find(x => x.km_from != null && x.km_to != null && k >= x.km_from && k <= x.km_to && x.km_to > x.km_from);
    if (!s) return null;
    return s.t0 + ((k - s.km_from!) / (s.km_to! - s.km_from!)) * (s.t1 - s.t0);
  };

  const dist = scale === 'distance' && hasKm;
  const X = (t: number) => pad + (dist ? (kmAt(t) / K1) * W : ((t - T0) / Math.max(1, T1 - T0)) * W);
  const tAtX = (x: number) => {
    const f = Math.min(1, Math.max(0, (x - pad) / W));
    if (!dist) return T0 + f * (T1 - T0);
    return timeAtKm(f * K1) ?? T0 + f * (T1 - T0);
  };

  // phases: consecutive segments of one phase
  const bands = useMemo(() => {
    const out: { phase: string; t0: number; t1: number; dur: number; name?: string }[] = [];
    for (const s of segs) {
      const last = out[out.length - 1];
      if (last && last.phase === s.phase) { last.t1 = s.t1; last.dur += s.duration_s; }
      else out.push({ phase: s.phase, t0: s.t0, t1: s.t1, dur: s.duration_s, name: s.kind === 'stay' ? s.name : undefined });
    }
    return out;
  }, [segs]);

  const spanTime = T1 - T0;
  const tStep = niceStep(spanTime, Math.max(3, Math.floor(W / 110)), TIME_STEPS);
  const timeTicks: number[] = [];
  if (!dist) {
    const first = Math.ceil(T0 / tStep) * tStep;
    for (let t = first; t <= T1; t += tStep) timeTicks.push(t);
  }
  const kStep = niceStep(K1, Math.max(3, Math.floor(W / 90)), KM_STEPS);
  const kmTicks: { k: number; t: number | null; label: boolean }[] = [];
  if (hasKm) {
    let lastX = -Infinity;
    for (let k = 0; k <= K1 + 1e-9; k += kStep) {
      const t = dist ? null : timeAtKm(k);
      const x = t == null ? pad + (k / K1) * W : pad + ((t - T0) / Math.max(1, T1 - T0)) * W;
      // A truck covers most of a ruler's marks in a few hours of fast driving:
      // those marks keep their tick, and only the ones with room keep a label.
      const label = x - lastX >= 46;
      if (label) lastX = x;
      kmTicks.push({ k, t, label });
    }
  }
  const withDay = spanTime > 20 * 3600_000;

  const H_PHASE = 20, Y_KM = 44, Y_BAR = 60, H_BAR = 26, Y_AXIS = Y_BAR + H_BAR + 10;
  const height = Y_AXIS + 30;

  const eventX = (e: React.MouseEvent) => {
    const r = (e.currentTarget as SVGElement).getBoundingClientRect();
    return e.clientX - r.left;
  };
  const segAtX = (x: number) => {
    const t = tAtX(x);
    return segs.find(s => t >= s.t0 && t <= s.t1) ?? null;
  };

  if (!segs.length) return <p className="text-sm text-gray-500 py-6 text-center">No GPS to lay out.</p>;
  const cx = cursor != null && cursor >= T0 && cursor <= T1 ? X(cursor) : null;

  return (
    <div>
      <div className="flex items-center justify-between gap-3 flex-wrap mb-2">
        <div className="flex flex-wrap gap-3 text-xs text-gray-400">
          {LEGEND.map(l => (
            <span key={l.label} className="inline-flex items-center gap-1.5">
              <span className="w-3 h-2.5 rounded-sm" style={{ background: segmentColor(l.phase, l.kind) }} />{l.label}
            </span>
          ))}
          <span className="inline-flex items-center gap-1.5 text-gray-500">
            <span className="w-3 h-2.5 rounded-sm opacity-40" style={{ background: PALETTE.blue }} />before / after the trip
          </span>
        </div>
        <div className="flex gap-1 bg-gray-900 border border-gray-800 rounded-lg p-0.5">
          {(['time', 'distance'] as const).map(s => (
            <button key={s} disabled={s === 'distance' && !hasKm} onClick={() => setScale(s)}
              className={`px-2.5 py-1 rounded-md text-xs font-medium capitalize disabled:opacity-30 ${
                scale === s ? 'bg-blue-600/15 text-blue-400' : 'text-gray-400 hover:text-gray-200'}`}>
              by {s}
            </button>
          ))}
        </div>
      </div>
      <div ref={wrap} className="relative select-none">
        <svg width={width} height={height} className="block"
          onMouseMove={e => {
            const x = eventX(e);
            const seg = segAtX(x);
            setHover(seg ? { x, seg } : null);
            if (dragging.current && onCursor) onCursor(tAtX(x));
          }}
          onMouseLeave={() => { setHover(null); dragging.current = false; }}
          onMouseDown={e => { dragging.current = true; onCursor?.(tAtX(eventX(e))); }}
          onMouseUp={e => {
            dragging.current = false;
            const seg = segAtX(eventX(e));
            if (seg) onSelect?.(seg);
          }}>
          {/* phases */}
          {bands.map((b, i) => {
            const x0 = X(b.t0), x1 = X(b.t1);
            const w = Math.max(dist && b.phase !== 'transit' ? 2 : 1, x1 - x0);
            const color = b.phase === 'loading' ? PALETTE.purple : b.phase === 'unloading' ? PALETTE.cyan
              : b.phase === 'transit' ? PALETTE.blue : PALETTE.gray;
            return (
              <g key={i}>
                <rect x={x0} y={2} width={w} height={H_PHASE} rx={4} fill={color} fillOpacity={0.14} stroke={color} strokeOpacity={0.5} />
                {w > 70 && (
                  <text x={x0 + 6} y={16} fontSize={12} fill={color} className="pointer-events-none">
                    {PHASE_LABEL[b.phase]} · {fmtDuration(b.dur)}{b.name && w > 220 ? ` · ${b.name}` : ''}
                  </text>
                )}
              </g>
            );
          })}

          {/* the kilometre ruler (or the clock ruler, by distance) */}
          <line x1={pad} x2={pad + W} y1={Y_KM + 6} y2={Y_KM + 6} stroke={PALETTE.grid} />
          {!dist && kmTicks.map(({ k, t, label }) => t == null ? null : (
            <g key={k}>
              <line x1={X(t)} x2={X(t)} y1={Y_KM + (label ? 2 : 5)} y2={Y_KM + 10} stroke={PALETTE.axis} />
              {label && <text x={X(t)} y={Y_KM - 2} fontSize={12} textAnchor="middle" fill={PALETTE.axis}>{k === 0 ? '0 km' : fmtKm(k)}</text>}
            </g>
          ))}
          {dist && (() => {
            const out = [];
            const step = niceStep(spanTime, Math.max(3, Math.floor(W / 110)), TIME_STEPS);
            for (let t = Math.ceil(T0 / step) * step; t <= T1; t += step) {
              out.push(
                <g key={t}>
                  <line x1={X(t)} x2={X(t)} y1={Y_KM + 2} y2={Y_KM + 10} stroke={PALETTE.axis} />
                  <text x={X(t)} y={Y_KM - 2} fontSize={12} textAnchor="middle" fill={PALETTE.axis}>{clock(t, withDay)}</text>
                </g>);
            }
            return out;
          })()}

          {/* the bar */}
          <rect x={pad} y={Y_BAR} width={W} height={H_BAR} rx={4} fill={PALETTE.grid} fillOpacity={0.5} />
          {segs.map(s => {
            const x0 = X(s.t0), x1 = X(s.t1);
            const color = segmentColor(s.phase, s.kind);
            const sel = selected === s.seq;
            if (dist && x1 - x0 < 1.5) {
              // a stay, a stop: a pin at its kilometre, taller for longer
              const h = Math.min(H_BAR + 14, 6 + Math.sqrt(s.duration_s / 60) * 2.2);
              return (
                <g key={s.seq}>
                  <line x1={x0} x2={x0} y1={Y_BAR + H_BAR} y2={Y_BAR + H_BAR - h} stroke={color} strokeWidth={sel ? 3 : 2}
                    strokeOpacity={segmentOpacity(s.phase)} />
                  <circle cx={x0} cy={Y_BAR + H_BAR - h} r={sel ? 4 : 3} fill={color} />
                </g>
              );
            }
            return (
              <rect key={s.seq} x={x0} y={Y_BAR} width={Math.max(0.8, x1 - x0)} height={H_BAR}
                fill={color} fillOpacity={segmentOpacity(s.phase)}
                stroke={sel ? PALETTE.tooltipText : 'none'} strokeWidth={sel ? 1.5 : 0}
                style={s.kind === 'silent' ? { fill: `url(#silent-${s.phase})` } : undefined} />
            );
          })}
          <defs>
            {['transit', 'before', 'after'].map(ph => (
              <pattern key={ph} id={`silent-${ph}`} width={6} height={6} patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
                <rect width={6} height={6} fill={PALETTE.red} fillOpacity={segmentOpacity(ph) * 0.35} />
                <line x1={0} y1={0} x2={0} y2={6} stroke={PALETTE.red} strokeWidth={3} strokeOpacity={segmentOpacity(ph)} />
              </pattern>
            ))}
          </defs>

          {/* the clock axis (or the kilometre axis, by distance) */}
          <line x1={pad} x2={pad + W} y1={Y_AXIS} y2={Y_AXIS} stroke={PALETTE.grid} />
          {!dist && timeTicks.map(t => {
            const midnight = new Date(t).getHours() === 0 && new Date(t).getMinutes() === 0;
            return (
              <g key={t}>
                <line x1={X(t)} x2={X(t)} y1={Y_AXIS} y2={Y_AXIS + (midnight ? 8 : 5)} stroke={midnight ? PALETTE.legend : PALETTE.axis} />
                <text x={X(t)} y={Y_AXIS + 18} fontSize={12} textAnchor="middle" fill={midnight ? PALETTE.legend : PALETTE.axis}>
                  {clock(t, withDay && (midnight || tStep >= 12 * 3600_000))}
                </text>
              </g>
            );
          })}
          {dist && kmTicks.map(({ k, label }) => (
            <g key={k}>
              <line x1={pad + (k / K1) * W} x2={pad + (k / K1) * W} y1={Y_AXIS} y2={Y_AXIS + 5} stroke={PALETTE.axis} />
              {label && <text x={pad + (k / K1) * W} y={Y_AXIS + 18} fontSize={12} textAnchor="middle" fill={PALETTE.axis}>{fmtKm(k)}</text>}
            </g>
          ))}

          {/* the playhead */}
          {cx != null && (
            <g className="pointer-events-none">
              <line x1={cx} x2={cx} y1={0} y2={Y_AXIS} stroke={PALETTE.tooltipText} strokeWidth={1.5} strokeDasharray="3 3" />
              <circle cx={cx} cy={Y_BAR + H_BAR / 2} r={5} fill={PALETTE.tooltipText} stroke={PALETTE.markerOutline} strokeWidth={2} />
            </g>
          )}
          {hover && <line x1={hover.x} x2={hover.x} y1={Y_BAR - 4} y2={Y_BAR + H_BAR + 4} stroke={PALETTE.legend} strokeOpacity={0.6} className="pointer-events-none" />}
        </svg>
        {hover && (
          <div className="absolute z-10 pointer-events-none rounded-lg border px-3 py-2 text-xs shadow-lg"
            style={{ left: Math.min(Math.max(0, hover.x - 130), width - 270), top: Y_BAR + H_BAR + 36, width: 260,
              background: PALETTE.tooltipBg, borderColor: PALETTE.tooltipBorder, color: PALETTE.tooltipText }}>
            <p className="font-medium flex items-center gap-1.5">
              <span className="w-2.5 h-2.5 rounded-sm" style={{ background: segmentColor(hover.seg.phase, hover.seg.kind) }} />
              {PHASE_LABEL[hover.seg.phase]} · {KIND_TEXT[hover.seg.kind] || hover.seg.kind}
            </p>
            {hover.seg.name && <p className="text-gray-400 mt-0.5">{hover.seg.name}</p>}
            <p className="text-gray-400 mt-1">{fmtDateTime(hover.seg.start)} → {hover.seg.open ? 'still there' : fmtDateTime(hover.seg.end)}</p>
            <p className="mt-0.5">
              <b>{fmtDuration(hover.seg.duration_s)}</b>
              {hover.seg.km_from != null && <> · km {hover.seg.km_from.toFixed(1)} → {hover.seg.km_to!.toFixed(1)}</>}
              {hover.seg.straight_km ? <> · {fmtKm(hover.seg.straight_km)} unobserved</> : null}
            </p>
          </div>
        )}
      </div>
    </div>
  );
}
