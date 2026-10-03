import { useEffect, useRef } from 'react';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';
import { addDarkBasemap } from '../../lib/basemap';
import { tc } from '../../../../core/theme';

export interface TrackPoint { lat: number; lng: number; moving: boolean }
export interface HotZone {
  lat: number; lng: number; minutes: number;
  label?: string | null; reason?: string | null;
}

const ZONE_COLORS: Record<string, string> = {
  'Unloading / Detention': tc('#a855f7'),
  'Loading / At plant': tc('#f59e0b'),
  'Night rest': '#6366f1',
  'Long halt': tc('#ef4444'),
  'Extended halt': '#f97316',
  'Lunch break': tc('#10b981'),
  'Dinner break': '#14b8a6',
  'Tea / short break': tc('#06b6d4'),
  'Halt': tc('#9ca3af'),
};

/**
 * Real map (dark CARTO tiles over OpenStreetMap) with the trip trace and
 * glowing stop hot-zones. Scroll-wheel zoom is OFF by default so casual
 * scrolling never hijacks the page — hold Ctrl (or use the +/- buttons).
 */
export default function LeafletTrackMap({ points, hotZones = [], height = 480 }: {
  points: TrackPoint[]; hotZones?: HotZone[]; height?: number;
}) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el || points.length < 2) return;

    const map = L.map(el, {
      scrollWheelZoom: false,
      zoomControl: true,
      attributionControl: true,
      preferCanvas: true,
      // animations off: React remounts (dev StrictMode) can destroy the map
      // mid-transition, which crashes Leaflet and leaves a half-drawn map
      zoomAnimation: false,
      fadeAnimation: false,
      markerZoomAnimation: false,
    });
    addDarkBasemap(map);

    // trace: split into runs of same motion state so stopped crawl shows amber
    let run: L.LatLngExpression[] = [];
    let runMoving = points[0].moving;
    const flush = () => {
      if (run.length > 1) {
        L.polyline(run, {
          color: runMoving ? tc('#3b82f6') : tc('#f59e0b'),
          weight: runMoving ? 3 : 4,
          opacity: 0.9,
        }).addTo(map);
      }
    };
    for (const p of points) {
      if (p.moving !== runMoving) {
        run.push([p.lat, p.lng]);
        flush();
        run = [[p.lat, p.lng]];
        runMoving = p.moving;
      } else {
        run.push([p.lat, p.lng]);
      }
    }
    flush();

    // glowing hot zones — radius scales with stop duration
    const maxMin = Math.max(...hotZones.map(z => z.minutes), 1);
    for (const z of hotZones) {
      const color = ZONE_COLORS[z.reason ?? 'Halt'] || tc('#9ca3af');
      const r = 10 + 26 * Math.sqrt(z.minutes / maxMin);
      // halo
      L.circleMarker([z.lat, z.lng], {
        radius: r, color, weight: 0, fillColor: color, fillOpacity: 0.18,
      }).addTo(map);
      // core
      L.circleMarker([z.lat, z.lng], {
        radius: Math.max(r * 0.35, 5), color: '#0b0f19', weight: 1.5,
        fillColor: color, fillOpacity: 0.9,
      }).addTo(map).bindPopup(
        `<b>${z.label ?? 'Stop'}</b><br/>${z.reason ?? ''}<br/>${Math.round(z.minutes)} min standstill`,
      );
    }

    // start / end markers
    const s = points[0], e = points[points.length - 1];
    L.circleMarker([s.lat, s.lng], { radius: 9, fillColor: '#22c55e', fillOpacity: 1, color: '#0b0f19', weight: 2 })
      .addTo(map).bindPopup('<b>Start</b>');
    L.circleMarker([e.lat, e.lng], { radius: 9, fillColor: tc('#ef4444'), fillOpacity: 1, color: '#0b0f19', weight: 2 })
      .addTo(map).bindPopup('<b>End</b>');

    map.fitBounds(L.latLngBounds(points.map(p => [p.lat, p.lng] as [number, number])),
      { padding: [36, 36], animate: false });

    // Ctrl + scroll = zoom; plain scroll scrolls the page
    const onWheel = (ev: WheelEvent) => {
      if (ev.ctrlKey) {
        if (!map.scrollWheelZoom.enabled()) map.scrollWheelZoom.enable();
      } else if (map.scrollWheelZoom.enabled()) {
        map.scrollWheelZoom.disable();
      }
    };
    el.addEventListener('wheel', onWheel, { passive: true });

    return () => {
      el.removeEventListener('wheel', onWheel);
      try { map.stop(); map.off(); map.remove(); } catch { /* destroyed mid-animation */ }
    };
  }, [points, hotZones]);

  if (points.length < 2) return <p className="text-gray-500 text-sm">Not enough GPS points to draw the map</p>;

  return (
    <div>
      <div ref={ref} style={{ height }} className="rounded-xl overflow-hidden border border-gray-800 relative z-0" />
      <div className="flex items-center justify-between flex-wrap gap-2 mt-2">
        <div className="flex items-center gap-4 text-xs text-gray-400">
          <span className="flex items-center gap-1.5"><span className="w-3 h-3 rounded-full bg-emerald-500 inline-block" /> Start</span>
          <span className="flex items-center gap-1.5"><span className="w-3 h-3 rounded-full bg-red-500 inline-block" /> End</span>
          <span className="flex items-center gap-1.5"><span className="w-6 h-0.5 bg-blue-500 inline-block" /> Moving</span>
          <span className="flex items-center gap-1.5"><span className="w-6 h-0.5 bg-amber-500 inline-block" /> Stopped crawl</span>
          <span className="flex items-center gap-1.5"><span className="w-3 h-3 rounded-full bg-purple-500/40 border border-purple-400 inline-block" /> Hot zone (size = standstill)</span>
        </div>
        <span className="text-xs text-gray-600">scroll passes through — hold Ctrl + scroll (or use +/−) to zoom</span>
      </div>
    </div>
  );
}
