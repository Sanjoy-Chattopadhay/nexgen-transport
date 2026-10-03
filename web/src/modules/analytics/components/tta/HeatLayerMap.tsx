import { useEffect, useRef } from 'react';
import L from 'leaflet';
import 'leaflet.heat';
import 'leaflet/dist/leaflet.css';
import { addDarkBasemap } from '../../lib/basemap';
import { tc } from '../../../../core/theme';

/**
 * India-wide GPS density heatmap (leaflet.heat over dark CARTO tiles).
 * cells: [lat, lng, intensity 0..1]. sqrt-boosted intensity + glow markers
 * on the hottest cells so sparse data still reads clearly.
 * Ctrl+scroll (or +/- buttons) to zoom.
 */
export default function HeatLayerMap({ cells, height = 520 }: {
  cells: [number, number, number][]; height?: number;
}) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;

    const map = L.map(el, {
      scrollWheelZoom: false,
      zoomControl: true,
      preferCanvas: true,
      // animations off: React remounts can destroy the map mid-transition
      zoomAnimation: false,
      fadeAnimation: false,
      markerZoomAnimation: false,
    }).setView([22.8, 80.5], 5); // India
    addDarkBasemap(map);

    if (cells.length > 0) {
      // sqrt boost: low-weight cells stay visible instead of vanishing
      const boosted = cells.map(c => [c[0], c[1], Math.max(Math.sqrt(c[2]), 0.15)]);
      (L as any).heatLayer(boosted, {
        radius: 30,
        blur: 20,
        maxZoom: 10,
        max: 1.0,
        minOpacity: 0.35,
        gradient: { 0.15: tc('#1d4ed8'), 0.35: tc('#06b6d4'), 0.55: '#22c55e', 0.75: '#eab308', 0.9: tc('#ef4444') },
      }).addTo(map);

      // glow markers + popups on the hottest cells so they're unmissable
      const top = [...cells].sort((a, b) => b[2] - a[2]).slice(0, 10);
      for (const [lat, lng, w] of top) {
        const r = 10 + 18 * Math.sqrt(w);
        L.circleMarker([lat, lng], {
          radius: r, weight: 0, fillColor: tc('#ef4444'), fillOpacity: 0.15,
        }).addTo(map);
        L.circleMarker([lat, lng], {
          radius: 4.5, weight: 1.5, color: '#0b0f19', fillColor: '#f97316', fillOpacity: 0.95,
        }).addTo(map).bindPopup(
          `<b>Hot zone</b><br/>${lat.toFixed(2)}, ${lng.toFixed(2)}<br/>intensity ${(w * 100).toFixed(0)}%`,
        );
      }

      if (cells.length > 2) {
        map.fitBounds(L.latLngBounds(cells.map(c => [c[0], c[1]] as [number, number])),
          { padding: [50, 50], maxZoom: 8, animate: false });
      }
    }

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
  }, [cells]);

  return (
    <div>
      <div ref={ref} style={{ height }} className="rounded-xl overflow-hidden border border-gray-800 relative z-0" />
      <div className="flex items-center justify-between flex-wrap gap-2 mt-1.5">
        <div className="flex items-center gap-2 text-xs text-gray-500">
          <span>low</span>
          <span className="h-2 w-40 rounded-full inline-block"
            style={{ background: `linear-gradient(to right, ${tc('#1d4ed8')}, ${tc('#06b6d4')}, #22c55e, #eab308, ${tc('#ef4444')})` }} />
          <span>high</span>
          <span className="ml-3 flex items-center gap-1.5">
            <span className="w-3 h-3 rounded-full bg-orange-500 inline-block" /> top hot zones (click for detail)
          </span>
        </div>
        <p className="text-xs text-gray-600">hold Ctrl + scroll (or use +/−) to zoom</p>
      </div>
    </div>
  );
}
