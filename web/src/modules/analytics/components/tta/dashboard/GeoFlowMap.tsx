import { useEffect, useRef } from 'react';
import L from 'leaflet';
import 'leaflet.heat';
import 'leaflet/dist/leaflet.css';
import { addDarkBasemap } from '../../../lib/basemap';
import { metricColor } from '../../../lib/colorScales';
import { tc } from '../../../../../core/theme';

export interface GeoPoint {
  destination: string;
  dest_lat: number;
  dest_lon: number;
  trips: number;
  otd_pct: number | null;
  avg_transit_hours: number | null;
  avg_distance_km: number | null;
  total_km: number | null;
}

interface Props {
  origin: { name: string; lat: number; lon: number };
  points: GeoPoint[];
  colorMetric: 'otd_pct' | 'avg_transit_hours' | 'avg_distance_km';
  showArcs: boolean;
  showBubbles: boolean;
  showHeat: boolean;
  height?: number;
}

/** Flow map: plant origin → destination arcs + volume bubbles + density heat. */
export default function GeoFlowMap({ origin, points, colorMetric, showArcs, showBubbles, showHeat, height = 540 }: Props) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;

    const map = L.map(el, {
      scrollWheelZoom: false, zoomControl: true, preferCanvas: true,
      zoomAnimation: false, fadeAnimation: false, markerZoomAnimation: false,
    }).setView([22.8, 82.5], 5);
    addDarkBasemap(map);

    // colour scale bounds for the chosen metric
    const vals = points.map(p => p[colorMetric]).filter((v): v is number => v != null && isFinite(v));
    const lo = vals.length ? Math.min(...vals) : 0;
    const hi = vals.length ? Math.max(...vals) : 1;
    const invert = colorMetric !== 'otd_pct'; // high transit/distance = red
    const colorOf = (p: GeoPoint) => metricColor(p[colorMetric], lo, hi, invert);

    if (showHeat && points.length) {
      const maxTrips = Math.max(...points.map(p => p.trips), 1);
      (L as any).heatLayer(
        points.map(p => [p.dest_lat, p.dest_lon, Math.max(Math.sqrt(p.trips / maxTrips), 0.15)]),
        {
          radius: 34, blur: 24, maxZoom: 9, max: 1.0, minOpacity: 0.3,
          gradient: { 0.15: tc('#1d4ed8'), 0.35: tc('#06b6d4'), 0.55: '#22c55e', 0.75: '#eab308', 0.9: tc('#ef4444') },
        }).addTo(map);
    }

    if (showArcs) {
      for (const p of points) {
        // simple quadratic-ish curve: midpoint offset perpendicular to the line
        const mLat = (origin.lat + p.dest_lat) / 2, mLon = (origin.lon + p.dest_lon) / 2;
        const dLat = p.dest_lat - origin.lat, dLon = p.dest_lon - origin.lon;
        const bend = 0.12;
        const c: [number, number] = [mLat - dLon * bend, mLon + dLat * bend];
        const curve: [number, number][] = [];
        for (let t = 0; t <= 1.001; t += 0.05) {
          const a = 1 - t;
          curve.push([
            a * a * origin.lat + 2 * a * t * c[0] + t * t * p.dest_lat,
            a * a * origin.lon + 2 * a * t * c[1] + t * t * p.dest_lon,
          ]);
        }
        L.polyline(curve, {
          color: colorOf(p), weight: 1 + Math.min(p.trips / 60, 5), opacity: 0.5,
        }).addTo(map).bindPopup(
          `<b>${origin.name} → ${p.destination}</b><br/>${p.trips} trips`,
        );
      }
    }

    if (showBubbles) {
      for (const p of points) {
        L.circleMarker([p.dest_lat, p.dest_lon], {
          radius: 5 + Math.sqrt(p.trips) * 1.6,
          weight: 1.5, color: '#0b0f19',
          fillColor: colorOf(p), fillOpacity: 0.8,
        }).addTo(map).bindPopup(
          `<b>${p.destination}</b><br/>` +
          `${p.trips} trips<br/>` +
          `OTD: ${p.otd_pct ?? '—'}%<br/>` +
          `Avg transit: ${p.avg_transit_hours ?? '—'} h<br/>` +
          `Avg distance: ${p.avg_distance_km ?? '—'} km`,
        );
      }
    }

    // origin marker (plant)
    L.circleMarker([origin.lat, origin.lon], {
      radius: 8, weight: 2.5, color: tc('#f9fafb'), fillColor: tc('#3b82f6'), fillOpacity: 1,
    }).addTo(map).bindPopup(`<b>${origin.name}</b><br/>Plant / origin`);

    if (points.length > 1) {
      map.fitBounds(
        L.latLngBounds([[origin.lat, origin.lon], ...points.map(p => [p.dest_lat, p.dest_lon] as [number, number])]),
        { padding: [40, 40], maxZoom: 7, animate: false });
    }

    const onWheel = (ev: WheelEvent) => {
      if (ev.ctrlKey) { if (!map.scrollWheelZoom.enabled()) map.scrollWheelZoom.enable(); }
      else if (map.scrollWheelZoom.enabled()) map.scrollWheelZoom.disable();
    };
    el.addEventListener('wheel', onWheel, { passive: true });

    return () => {
      el.removeEventListener('wheel', onWheel);
      try { map.stop(); map.off(); map.remove(); } catch { /* mid-animation */ }
    };
  }, [origin, points, colorMetric, showArcs, showBubbles, showHeat]);

  return (
    <div>
      <div ref={ref} style={{ height }} className="rounded-xl overflow-hidden border border-gray-800 relative z-0" />
      <div className="flex items-center justify-between flex-wrap gap-2 mt-1.5">
        <div className="flex items-center gap-2 text-xs text-gray-500">
          <span>{colorMetric === 'otd_pct' ? 'low OTD' : 'best'}</span>
          <span className="h-2 w-36 rounded-full inline-block"
            style={{ background: 'linear-gradient(to right, #f94144, #f9c74f, #90be6d)' }} />
          <span>{colorMetric === 'otd_pct' ? 'high OTD' : 'worst'}</span>
          <span className="ml-3 flex items-center gap-1.5">
            <span className="w-3 h-3 rounded-full bg-blue-500 border border-white inline-block" /> plant origin
          </span>
        </div>
        <p className="text-xs text-gray-600">bubble size = trips · hold Ctrl + scroll to zoom</p>
      </div>
    </div>
  );
}
