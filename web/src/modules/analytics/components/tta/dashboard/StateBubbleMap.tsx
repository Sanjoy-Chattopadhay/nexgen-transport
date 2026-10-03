import { useEffect, useRef } from 'react';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';
import { addDarkBasemap } from '../../../lib/basemap';
import { metricColor } from '../../../lib/colorScales';
import type { StateRow } from '../../../services/ttaDashboard';
import { tc } from '../../../../../core/theme';

export type BubbleMode = 'volume' | 'ontime';

interface Props {
  states: StateRow[];
  origin?: { name: string; lat: number; lon: number };
  mode: BubbleMode;
  height?: number;
  /** Below this many judged trips, an on-time rate is not drawn as a verdict. */
  minJudged?: number;
  onSelect?: (state: string) => void;
}

const GREY = tc('#4b5563');

/**
 * One bubble per state.
 *
 * Why state and not destination: at destination grain this map was 126
 * overlapping circles, most of them one or two trips, and it could not be read
 * for either of the two things it is asked — where the freight goes, and where
 * service is bad. A state aggregates enough trips for an on-time rate to mean
 * something.
 *
 * Two modes because the two questions want different encodings:
 *
 *   volume  size = trips, colour = trips. "Where does our freight go?"
 *   ontime  size = trips, colour = on-time rate. Size still carries volume so
 *           a red pinprick is not mistaken for a red crisis.
 *
 * In on-time mode a state with fewer than `minJudged` delivered trips is drawn
 * grey rather than coloured. Two late trips out of three is not a 33% service
 * failure, and colouring it red would send someone to fix a region on the
 * strength of three data points.
 */
export default function StateBubbleMap({
  states, origin, mode, height = 480, minJudged = 10, onSelect,
}: Props) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;

    const map = L.map(el, {
      scrollWheelZoom: false, zoomControl: true, preferCanvas: true,
      zoomAnimation: false, fadeAnimation: false, markerZoomAnimation: false,
    }).setView([22.0, 80.0], 4);
    addDarkBasemap(map);

    const drawable = states.filter(s => s.lat != null && s.lon != null);
    const maxTrips = Math.max(...drawable.map(s => s.trips), 1);

    for (const s of drawable) {
      // Radius by sqrt of volume: a circle's AREA should carry the magnitude,
      // otherwise a state with 4x the freight looks 16x bigger.
      const radius = 8 + 34 * Math.sqrt(s.trips / maxTrips);
      const thin = mode === 'ontime' && s.judged_trips < minJudged;
      const color = mode === 'volume'
        ? metricColor(s.trips, 0, maxTrips, false)
        : thin || s.otd_pct == null
          ? GREY
          : metricColor(s.otd_pct, 60, 100, false);

      const circle = L.circleMarker([s.lat as number, s.lon as number], {
        radius, color, weight: 1.5, fillColor: color,
        fillOpacity: thin ? 0.25 : 0.55,
      }).addTo(map);

      circle.bindTooltip(
        `<div style="font-size: 13px">
           <b>${s.state}</b><br/>
           ${s.trips.toLocaleString('en-IN')} trips (${s.share_pct ?? 0}% of freight)<br/>
           ${s.otd_pct == null ? 'no delivery status recorded'
            : `OTD ${s.otd_pct}% on ${s.judged_trips} judged trips` +
              (s.otd_ci_low != null ? ` (${s.otd_ci_low}–${s.otd_ci_high}% at 95%)` : '')}<br/>
           avg transit ${s.avg_transit_hours ?? '—'} h · ${s.destinations} destinations
           ${thin ? '<br/><i>too few judged trips to colour</i>' : ''}
         </div>`,
        { direction: 'top', opacity: 0.95 });

      if (onSelect) {
        circle.on('click', () => onSelect(s.state));
      }
    }

    if (origin) {
      L.circleMarker([origin.lat, origin.lon], {
        radius: 6, color: tc('#fbbf24'), weight: 2, fillColor: tc('#fbbf24'), fillOpacity: 1,
      }).addTo(map).bindTooltip(`Origin — ${origin.name}`, { direction: 'top' });
    }

    if (drawable.length) {
      // animate:false, and the teardown below is guarded, for the same reason
      // GeoFlowMap does it: StrictMode mounts the effect, tears it down and
      // mounts it again, and tearing a map down mid-pan throws out of the
      // cleanup — which leaves the second mount's canvas never painted, with
      // tiles still showing so the map looks merely empty rather than broken.
      map.fitBounds(L.latLngBounds(
        drawable.map(s => [s.lat as number, s.lon as number] as [number, number])),
        { padding: [40, 40], maxZoom: 6, animate: false });
    }

    return () => {
      try { map.stop(); map.off(); map.remove(); } catch { /* mid-animation */ }
    };
  }, [states, origin, mode, minJudged, onSelect]);

  return <div ref={ref} style={{ height }} className="rounded-lg overflow-hidden" />;
}
