import L from 'leaflet';
import { PALETTE } from '../../../core/theme';

/**
 * The base map every fleet map draws on: India from its own official geometry.
 *
 * Why this file exists
 * --------------------
 * These maps used to load Esri's World Dark Gray tiles (and, before that,
 * CARTO's, which began watermarking unkeyed requests). Every commercial tile
 * provider bakes the international depiction of India's northern border into
 * its images, so NexGen draws no tiles at all: India is drawn from the
 * official state geometry the geofence service serves (data/india_states.json,
 * checked by geo/india.py's verify_extent), exactly as the geofence maps do.
 * It also means the maps work with no internet connection.
 *
 * What is lost is the street and place-name detail the tiles carried. The
 * trace, stops, fences and markers every page draws are unchanged and still
 * sit above the base layer. All call sites keep calling addDarkBasemap(map).
 */

let statesPromise: Promise<unknown> | null = null;

function loadStates(): Promise<unknown> {
  if (!statesPromise) {
    // The same URL the geofence maps use, so the browser caches it once.
    statesPromise = fetch('/api/v1/geo/map/states?v=2')
      .then(r => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      })
      .catch(err => { statesPromise = null; throw err; });
  }
  return statesPromise;
}

/** Draw India's states under everything else on the map. */
export function addDarkBasemap(map: L.Map): void {
  if (!map.getPane('base')) {
    map.createPane('base');
    map.getPane('base')!.style.zIndex = '200';
  }
  loadStates()
    .then(gj => {
      // The map may have been removed (page left) before the geometry arrived.
      if (!map.getPane('base')) return;
      L.geoJSON(gj as any, {
        pane: 'base',
        interactive: false,
        // nonzero: neighbouring rings overlap by slivers that evenodd would cut out.
        style: { color: PALETTE.mapStroke, weight: 0.8, fillColor: PALETTE.mapFill, fillOpacity: 1, fillRule: 'nonzero' },
      }).addTo(map);
    })
    .catch(err => console.error('India base map could not be drawn', err));
}
