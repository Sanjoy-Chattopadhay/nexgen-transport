import { useEffect, useRef } from 'react';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';
import { addDarkBasemap } from '../../lib/basemap';
import { tc } from '../../../../core/theme';

/**
 * One trip drawn against its geofences.
 *
 * The whole argument this page makes is visual: the trail is painted RED for
 * every ping still inside the origin fence and BLUE once the truck is genuinely
 * out. The gate-out stamp is dropped on the trail as an amber marker. When the
 * red segment continues past that marker — which it does on 453 of 649 trips —
 * the client is looking directly at detention their TMS did not report, rather
 * than at a median in a table.
 *
 * Fence circles are drawn to true scale in metres (`L.circle`, not
 * `circleMarker`), so a 15 km origin fence looks like 15 km at every zoom. That
 * matters: the Jamshedpur fence covers the whole works belt, and a reader who
 * cannot see how much ground it covers cannot judge the claim.
 */

export interface RoutePoint {
  lat: number;
  lon: number;
  t: string;
  speed: number | null;
  in_origin: boolean;
}

export interface FenceShape {
  key: string;
  name: string;
  role: string;
  lat: number;
  lon: number;
  radius_m: number;
  source?: string | null;
  /** Draw dashed, for a fence that is proposed rather than in force. */
  proposed?: boolean;
  label?: string;
}

/** Where the fence centre came from — the client needs to see this distinction. */
const SOURCE_COLOR: Record<string, string> = {
  client: '#22c55e',            // supplied by the client — authoritative
  anchor: tc('#3b82f6'),            // derived from the GPS trail, corroborated
  anchor_unverified: tc('#f59e0b'), // derived, nothing to check it against
  gazetteer: tc('#a855f7'),         // anchor rejected, town centroid used instead
  manual: '#14b8a6',
};

const SOURCE_LABEL: Record<string, string> = {
  client: 'Client-supplied',
  anchor: 'Derived from GPS (corroborated)',
  anchor_unverified: 'Derived from GPS (unverified)',
  gazetteer: 'Town centroid (GPS anchor rejected)',
  manual: 'Hand-corrected',
};

function fenceColor(f: FenceShape): string {
  return SOURCE_COLOR[f.source ?? ''] ?? '#64748b';
}

export default function GeofenceMap({
  route = [],
  fences = [],
  exitPoint = null,
  height = 520,
  showLegend = true,
}: {
  route?: RoutePoint[];
  fences?: FenceShape[];
  /** The stamped exit from the origin fence, if one was confirmed. */
  exitPoint?: { lat: number; lon: number; t: string } | null;
  height?: number;
  showLegend?: boolean;
}) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el || (route.length === 0 && fences.length === 0)) return;

    const map = L.map(el, {
      scrollWheelZoom: false,
      preferCanvas: true,
      // Animations off: a React remount (dev StrictMode) can destroy the map
      // mid-transition, which crashes Leaflet and leaves a half-drawn canvas.
      zoomAnimation: false,
      fadeAnimation: false,
      markerZoomAnimation: false,
    });
    addDarkBasemap(map);

    const bounds: [number, number][] = [];

    // -- fence circles, true scale ------------------------------------------
    for (const f of fences) {
      const color = fenceColor(f);
      L.circle([f.lat, f.lon], {
        radius: f.radius_m,
        color,
        weight: f.proposed ? 2 : 1.5,
        dashArray: f.proposed ? '6 6' : undefined,
        fillColor: color,
        fillOpacity: 0.07,
      })
        .addTo(map)
        .bindPopup(
          `<b>${f.name}</b><br/>${f.role} fence` +
            `<br/>radius ${(f.radius_m / 1000).toFixed(f.radius_m < 1000 ? 2 : 1)} km` +
            `<br/><span style="opacity:.75">${
              f.label ?? SOURCE_LABEL[f.source ?? ''] ?? 'source unknown'
            }</span>`,
        );
      L.circleMarker([f.lat, f.lon], {
        radius: 4,
        color,
        weight: 1,
        fillColor: color,
        fillOpacity: 0.95,
      }).addTo(map);

      // Include the fence's extent, not just its centre, or a 15 km circle
      // gets cropped by fitBounds.
      const dLat = f.radius_m / 111_320;
      const dLon = f.radius_m / (111_320 * Math.cos((f.lat * Math.PI) / 180) || 1);
      bounds.push([f.lat - dLat, f.lon - dLon], [f.lat + dLat, f.lon + dLon]);
    }

    // -- the trail, split into runs of the same in/out state -----------------
    if (route.length > 1) {
      let run: [number, number][] = [];
      let runInside = route[0].in_origin;
      const flush = () => {
        if (run.length > 1) {
          L.polyline(run, {
            color: runInside ? tc('#ef4444') : tc('#3b82f6'),
            weight: runInside ? 4 : 3,
            opacity: runInside ? 0.95 : 0.8,
          }).addTo(map);
        }
      };
      for (const p of route) {
        const here: [number, number] = [p.lat, p.lon];
        if (p.in_origin !== runInside) {
          run.push(here);
          flush();
          run = [here];
          runInside = p.in_origin;
        } else {
          run.push(here);
        }
        bounds.push(here);
      }
      flush();

      const first = route[0];
      const last = route[route.length - 1];
      L.circleMarker([first.lat, first.lon], {
        radius: 8, fillColor: '#22c55e', fillOpacity: 1, color: '#0b0f19', weight: 2,
      })
        .addTo(map)
        .bindPopup(`<b>First ping</b><br/>${first.t}`);
      L.circleMarker([last.lat, last.lon], {
        radius: 8, fillColor: tc('#ef4444'), fillOpacity: 1, color: '#0b0f19', weight: 2,
      })
        .addTo(map)
        .bindPopup(`<b>Last ping</b><br/>${last.t}`);
    }

    // -- the stamped exit ----------------------------------------------------
    if (exitPoint) {
      L.circleMarker([exitPoint.lat, exitPoint.lon], {
        radius: 11, fillColor: tc('#f59e0b'), fillOpacity: 0.25, color: tc('#f59e0b'), weight: 2,
      }).addTo(map);
      L.circleMarker([exitPoint.lat, exitPoint.lon], {
        radius: 5, fillColor: tc('#f59e0b'), fillOpacity: 1, color: '#0b0f19', weight: 2,
      })
        .addTo(map)
        .bindPopup(`<b>Confirmed exit from the origin fence</b><br/>${exitPoint.t}`);
      bounds.push([exitPoint.lat, exitPoint.lon]);
    }

    if (bounds.length) {
      map.fitBounds(L.latLngBounds(bounds), { padding: [30, 30], animate: false });
    }

    // Ctrl + scroll zooms; a plain scroll scrolls the page past the map.
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
      try {
        map.stop();
        map.off();
        map.remove();
      } catch {
        /* already destroyed mid-animation */
      }
    };
  }, [route, fences, exitPoint]);

  if (route.length === 0 && fences.length === 0) {
    return (
      <p className="text-gray-500 text-sm py-8 text-center">
        Nothing to draw — no GPS trail and no fence for this trip.
      </p>
    );
  }

  const sourcesShown = Array.from(
    new Set(fences.map(f => f.source).filter(Boolean) as string[]),
  );

  return (
    <div>
      <div
        ref={ref}
        style={{ height }}
        className="rounded-xl overflow-hidden border border-gray-800 relative z-0"
      />
      {showLegend && (
        <div className="flex items-start justify-between flex-wrap gap-3 mt-2">
          <div className="flex items-center gap-4 flex-wrap text-xs text-gray-400">
            {route.length > 0 && (
              <>
                <span className="flex items-center gap-1.5">
                  <span className="w-6 h-1 bg-red-500 inline-block rounded" />
                  Still inside the origin fence
                </span>
                <span className="flex items-center gap-1.5">
                  <span className="w-6 h-0.5 bg-blue-500 inline-block rounded" />
                  Clear of the fence, on route
                </span>
                <span className="flex items-center gap-1.5">
                  <span className="w-3 h-3 rounded-full bg-amber-500 inline-block" />
                  Confirmed exit
                </span>
              </>
            )}
            {sourcesShown.map(s => (
              <span key={s} className="flex items-center gap-1.5">
                <span
                  className="w-3 h-3 rounded-full inline-block border"
                  style={{
                    borderColor: SOURCE_COLOR[s] ?? '#64748b',
                    backgroundColor: `${SOURCE_COLOR[s] ?? '#64748b'}33`,
                  }}
                />
                {SOURCE_LABEL[s] ?? s}
              </span>
            ))}
          </div>
          <span className="text-xs text-gray-600 whitespace-nowrap">
            hold Ctrl + scroll to zoom
          </span>
        </div>
      )}
    </div>
  );
}
