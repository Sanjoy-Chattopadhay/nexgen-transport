/**
 * The one map component every page uses.
 *
 * No tile basemap by default. India is drawn from the application's own
 * state geometry (official depiction, served from /map/states), so the map
 * shows no foreign border depiction and works with no internet connection.
 *
 * Satellite imagery is an explicit opt-in toggle, and even then only loads at
 * plant-level zoom (12+): it is for checking a fence's shape against the real
 * yard, where it is invaluable, and it never renders a country-scale view.
 * Imagery tiles carry no borders or labels.
 */
import { useEffect, useRef, useState, type ReactNode } from 'react';
import L from 'leaflet';
import { Layers, Satellite } from 'lucide-react';
import { api } from '../../lib/api';
import { PALETTE } from '../../lib/theme';

let statesPromise: Promise<any> | null = null;
function loadStates() {
  if (!statesPromise) statesPromise = api.states().catch(err => { statesPromise = null; throw err; });
  return statesPromise;
}

let districtsPromise: Promise<any> | null = null;
function loadDistricts() {
  if (!districtsPromise) districtsPromise = api.districts().catch(err => { districtsPromise = null; throw err; });
  return districtsPromise;
}

const IMAGERY = 'https://services.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}';
const IMAGERY_MIN_ZOOM = 12;

export const INDIA_BOUNDS: L.LatLngBoundsExpression = [[6.5, 68.0], [37.5, 97.5]];

export default function GeoMap({ height = 420, onReady, children, className = '', districts = false,
  initialBounds, satellite: satelliteDefault = false }: {
  height?: number | string;
  onReady?: (map: L.Map) => void;
  children?: ReactNode;
  className?: string;
  districts?: boolean;
  initialBounds?: L.LatLngBoundsExpression;
  satellite?: boolean;
}) {
  const el = useRef<HTMLDivElement>(null);
  const mapRef = useRef<L.Map | null>(null);
  const imagery = useRef<L.TileLayer | null>(null);
  const districtLayer = useRef<L.GeoJSON | null>(null);
  const [satellite, setSatellite] = useState(satelliteDefault);
  const [showDistricts, setShowDistricts] = useState(districts);
  const [zoom, setZoom] = useState(5);
  const [baseError, setBaseError] = useState(false);

  useEffect(() => {
    if (!el.current || mapRef.current) return;
    const map = L.map(el.current, {
      zoomControl: true, attributionControl: false, preferCanvas: true,
      minZoom: 4, maxZoom: 19, worldCopyJump: false,
    });
    map.fitBounds(initialBounds || INDIA_BOUNDS);
    mapRef.current = map;
    map.on('zoomend', () => setZoom(map.getZoom()));
    setZoom(map.getZoom());

    // A pane below the overlays for the base geometry, so fences and tracks
    // added later always draw above India's fill.
    map.createPane('base');
    map.getPane('base')!.style.zIndex = '200';
    // Imagery sits above India's fill (so it is visible when switched on) and
    // below every overlay (so fences and tracks stay on top of it).
    map.createPane('imagery');
    map.getPane('imagery')!.style.zIndex = '250';
    loadStates().then(gj => {
      if (mapRef.current !== map) return;
      L.geoJSON(gj, {
        pane: 'base', interactive: false,
        // nonzero rather than Leaflet's evenodd: neighbouring district rings
        // overlap by slivers, and evenodd would cut those out of the fill.
        style: { color: PALETTE.mapStroke, weight: 0.8, fillColor: PALETTE.mapFill, fillOpacity: 1, fillRule: 'nonzero' },
      }).addTo(map);
    }).catch(err => {
      // Fences, tracks and vehicles still draw without it, but a map with no
      // India must say so rather than look empty.
      console.error('India base map could not be drawn', err);
      if (mapRef.current === map) setBaseError(true);
    });

    onReady?.(map);
    const ro = new ResizeObserver(() => map.invalidateSize());
    ro.observe(el.current);
    return () => { ro.disconnect(); map.remove(); mapRef.current = null; };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    if (satellite && !imagery.current) {
      imagery.current = L.tileLayer(IMAGERY, { minZoom: IMAGERY_MIN_ZOOM, maxZoom: 19, pane: 'imagery', opacity: 0.9 });
      imagery.current.addTo(map);
    } else if (!satellite && imagery.current) {
      imagery.current.remove();
      imagery.current = null;
    }
  }, [satellite]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    if (showDistricts && !districtLayer.current) {
      loadDistricts().then(gj => {
        if (!mapRef.current || districtLayer.current) return;
        districtLayer.current = L.geoJSON(gj, {
          pane: 'base', interactive: false,
          style: { color: PALETTE.mapDistrict, weight: 0.4, fill: false, dashArray: '2 3' },
        }).addTo(mapRef.current);
      }).catch(err => console.error('district boundaries could not be drawn', err));
    } else if (!showDistricts && districtLayer.current) {
      districtLayer.current.remove();
      districtLayer.current = null;
    }
  }, [showDistricts]);

  return (
    <div className={`relative rounded-lg overflow-hidden border border-gray-800 ${className}`} style={{ height }}>
      <div ref={el} className="absolute inset-0" />
      <div className="absolute top-2 right-2 z-[500] flex flex-col items-end gap-1.5">
        <div className="flex gap-1">
          <button onClick={() => setShowDistricts(v => !v)} title="District boundaries"
            className={`flex items-center gap-1 px-2 py-1 rounded-md text-xs border ${showDistricts
              ? 'bg-blue-600/20 border-blue-700 text-blue-300' : 'bg-gray-900/90 border-gray-700 text-gray-400 hover:text-gray-200'}`}>
            <Layers className="w-3.5 h-3.5" /> Districts
          </button>
          <button onClick={() => setSatellite(v => !v)}
            title="Satellite imagery (loads from Esri at zoom 12 and closer; needs internet)"
            className={`flex items-center gap-1 px-2 py-1 rounded-md text-xs border ${satellite
              ? 'bg-blue-600/20 border-blue-700 text-blue-300' : 'bg-gray-900/90 border-gray-700 text-gray-400 hover:text-gray-200'}`}>
            <Satellite className="w-3.5 h-3.5" /> Satellite
          </button>
        </div>
        {baseError && (
          <span className="px-2 py-1 rounded-md text-xs bg-gray-900/90 border border-amber-700 text-amber-300">
            India map could not be drawn
          </span>
        )}
        {satellite && zoom < IMAGERY_MIN_ZOOM && (
          <span className="px-2 py-1 rounded-md text-xs bg-gray-900/90 border border-gray-700 text-gray-400">
            zoom in to see imagery
          </span>
        )}
      </div>
      {children}
    </div>
  );
}
