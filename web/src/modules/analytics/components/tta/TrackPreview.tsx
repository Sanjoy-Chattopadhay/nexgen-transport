import { useMemo } from 'react';
import type { TTAGpsPoint } from '../../types/tta';
import { tc } from '../../../../core/theme';

export default function TrackPreview({ points, height = 420 }: { points: TTAGpsPoint[]; height?: number }) {
  const path = useMemo(() => {
    if (points.length < 2) return null;
    const lats = points.map(p => p.d_lat);
    const lngs = points.map(p => p.d_long);
    const minLat = Math.min(...lats), maxLat = Math.max(...lats);
    const minLng = Math.min(...lngs), maxLng = Math.max(...lngs);
    const spanLat = Math.max(maxLat - minLat, 1e-5);
    const spanLng = Math.max(maxLng - minLng, 1e-5);
    const W = 800, H = height, PAD = 20;
    const x = (lng: number) => PAD + ((lng - minLng) / spanLng) * (W - 2 * PAD);
    const y = (lat: number) => H - PAD - ((lat - minLat) / spanLat) * (H - 2 * PAD);
    const segs: { d: string; moving: boolean }[] = [];
    for (let i = 1; i < points.length; i++) {
      segs.push({
        d: `M ${x(points[i - 1].d_long).toFixed(1)} ${y(points[i - 1].d_lat).toFixed(1)} L ${x(points[i].d_long).toFixed(1)} ${y(points[i].d_lat).toFixed(1)}`,
        moving: points[i].is_moving === 1,
      });
    }
    const stops = points.filter(p => p.is_moving === 0);
    return {
      segs,
      stops: stops.map(p => ({ cx: x(p.d_long), cy: y(p.d_lat) })),
      start: { cx: x(points[0].d_long), cy: y(points[0].d_lat) },
      end: { cx: x(points[points.length - 1].d_long), cy: y(points[points.length - 1].d_lat) },
      W, H,
    };
  }, [points, height]);

  if (!path) return <p className="text-gray-500 text-sm">Not enough GPS points to draw a track</p>;

  return (
    <div>
      <svg viewBox={`0 0 ${path.W} ${path.H}`} className="w-full rounded-lg bg-gray-950 border border-gray-800">
        {path.segs.map((s, i) => (
          <path key={i} d={s.d} stroke={s.moving ? tc('#3b82f6') : tc('#f59e0b')} strokeWidth={2} fill="none" strokeLinecap="round" />
        ))}
        {path.stops.map((s, i) => (
          <circle key={i} cx={s.cx} cy={s.cy} r={2.5} fill={tc(tc('#f59e0b'))} opacity={0.5} />
        ))}
        <circle cx={path.start.cx} cy={path.start.cy} r={7} fill="#22c55e" stroke="#0a0a0a" strokeWidth={2} />
        <circle cx={path.end.cx} cy={path.end.cy} r={7} fill={tc(tc('#ef4444'))} stroke="#0a0a0a" strokeWidth={2} />
      </svg>
      <div className="flex items-center gap-5 mt-2 text-xs text-gray-400">
        <span className="flex items-center gap-1.5"><span className="w-3 h-3 rounded-full bg-emerald-500 inline-block" /> Start</span>
        <span className="flex items-center gap-1.5"><span className="w-3 h-3 rounded-full bg-red-500 inline-block" /> End</span>
        <span className="flex items-center gap-1.5"><span className="w-6 h-0.5 bg-blue-500 inline-block" /> Moving</span>
        <span className="flex items-center gap-1.5"><span className="w-6 h-0.5 bg-amber-500 inline-block" /> Stopped</span>
      </div>
    </div>
  );
}
