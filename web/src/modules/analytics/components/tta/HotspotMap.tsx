import { useEffect, useRef } from 'react';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';
import { addDarkBasemap } from '../../lib/basemap';
import type { Hotspot } from '../../services/hotspots';
import { tc } from '../../../../core/theme';

/**
 * Hotspot cluster map (dark CARTO tiles, same basemap as HeatLayerMap).
 *
 * One circle per cluster: radius grows with stop count, colour with score.
 * The selected cluster gets a halo so the map and the table stay in sync.
 *
 * Every popup carries the "lead, not evidence" framing and a satellite link —
 * the fastest way to resolve a coordinate is still to look at it, and that is
 * exactly the manual check this feature is meant to feed.
 */

const scoreColor = (score: number) =>
  score >= 80 ? tc('#ef4444') : score >= 70 ? tc('#f59e0b') : score >= 60 ? '#eab308' : tc('#3b82f6');

export default function HotspotMap({
  hotspots,
  selectedId,
  onSelect,
  height = 460,
}: {
  hotspots: Hotspot[];
  selectedId: number | null;
  onSelect: (id: number) => void;
  height?: number;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const mapRef = useRef<L.Map | null>(null);
  const layerRef = useRef<L.LayerGroup | null>(null);
  // Kept in a ref so redrawing markers never needs the callback in deps
  // (a new function identity each render would tear the map down every time).
  const onSelectRef = useRef(onSelect);
  onSelectRef.current = onSelect;

  useEffect(() => {
    const el = ref.current;
    if (!el) return;

    const map = L.map(el, {
      scrollWheelZoom: false,
      preferCanvas: true,
      // Animations off: React remounts can destroy the map mid-transition.
      zoomAnimation: false,
      fadeAnimation: false,
      markerZoomAnimation: false,
    }).setView([22.8, 82.5], 5);

    addDarkBasemap(map);

    mapRef.current = map;
    layerRef.current = L.layerGroup().addTo(map);
    return () => {
      map.remove();
      mapRef.current = null;
      layerRef.current = null;
    };
  }, []);

  useEffect(() => {
    const map = mapRef.current;
    const layer = layerRef.current;
    if (!map || !layer) return;

    layer.clearLayers();
    if (!hotspots.length) return;

    const maxStops = Math.max(...hotspots.map(h => h.i_stops), 1);

    hotspots.forEach(h => {
      const lat = Number(h.d_lat);
      const lng = Number(h.d_long);
      const score = Number(h.d_score);
      const selected = h.id === selectedId;
      // sqrt so a 130-stop cluster does not swamp a 15-stop one
      const radius = 6 + 14 * Math.sqrt(h.i_stops / maxStops);

      if (selected) {
        L.circleMarker([lat, lng], {
          radius: radius + 8,
          color: '#f8fafc',
          weight: 1.5,
          opacity: 0.9,
          fill: false,
        }).addTo(layer);
      }

      const marker = L.circleMarker([lat, lng], {
        radius,
        color: scoreColor(score),
        fillColor: scoreColor(score),
        fillOpacity: 0.55,
        weight: selected ? 2.5 : 1,
      }).addTo(layer);

      const satellite = `https://www.google.com/maps/@${lat},${lng},350m/data=!3m1!1e3`;
      marker.bindPopup(
        `<div style="font-family:system-ui;font-size: 13px;line-height:1.5;min-width:210px">
           <div style="font-weight:600;font-size:13px">Score ${score.toFixed(1)}</div>
           <div style="color:${tc('#9ca3af')}">${lat.toFixed(4)}, ${lng.toFixed(4)}</div>
           <hr style="margin:6px 0;border:none;border-top:1px solid ${tc('#374151')}"/>
           <div>${h.i_stops} stops &middot; ${h.i_vehicles} vehicles &middot; ${h.i_carriers} carriers</div>
           <div>median dwell ${Math.round(Number(h.d_median_dwell_min ?? 0))} min</div>
           <div>${((Number(h.d_night_share ?? 0)) * 100).toFixed(0)}% at night</div>
           <div style="color:${tc('#9ca3af')};margin-top:4px">near ${h.s_nearest_node ?? '—'} (${h.i_nearest_node_m ?? '?'} m)</div>
           <a href="${satellite}" target="_blank" rel="noopener noreferrer"
              style="display:inline-block;margin-top:6px;color:${tc('#60a5fa')}">Open satellite view &rarr;</a>
           <div style="margin-top:6px;color:${tc('#6b7280')};font-size:13px">Investigation lead, not evidence of theft.</div>
         </div>`,
      );
      marker.on('click', () => onSelectRef.current(h.id));
    });

    const bounds = L.latLngBounds(hotspots.map(h => [Number(h.d_lat), Number(h.d_long)]));
    if (bounds.isValid()) map.fitBounds(bounds, { padding: [40, 40], maxZoom: 9 });
  }, [hotspots, selectedId]);

  return (
    <div>
      <div ref={ref} style={{ height }} className="rounded-lg overflow-hidden border border-gray-800" />
      <div className="flex items-center gap-4 mt-2 text-xs text-gray-500">
        <span>Score:</span>
        {[[tc('#ef4444'), '80+'], [tc('#f59e0b'), '70-80'], ['#eab308', '60-70'], [tc('#3b82f6'), '<60']].map(([c, l]) => (
          <span key={l} className="flex items-center gap-1.5">
            <span className="inline-block w-2.5 h-2.5 rounded-full" style={{ background: c }} />
            {l}
          </span>
        ))}
        <span className="ml-auto">Circle size = stop count &middot; click a circle for detail</span>
      </div>
    </div>
  );
}
