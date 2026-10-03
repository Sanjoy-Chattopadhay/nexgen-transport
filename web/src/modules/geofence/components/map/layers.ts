import L from 'leaflet';
import { PALETTE } from '../../lib/theme';

/** Fence colour by what the fence is for, then by how big it is. */
export function fenceColor(category?: string | null, scale?: string | null): string {
  const f = PALETTE.fence;
  if (category === 'restricted') return f.restricted;
  if (category === 'high_risk') return f.high_risk;
  switch (scale) {
    case 'micro': return f.micro;
    case 'site': return f.site;
    case 'campus': return f.campus;
    default: return f.regional;
  }
}

export interface RingFence {
  site_id: number;
  name: string;
  category?: string | null;
  scale?: string | null;
  ring: [number, number][];
}

export function fencePolygon(f: RingFence, opts: { focus?: boolean; dim?: boolean; onClick?: () => void } = {}) {
  const color = fenceColor(f.category, f.scale);
  const poly = L.polygon(f.ring, {
    color,
    weight: opts.focus ? 2.5 : 1.2,
    opacity: opts.dim ? 0.45 : 0.95,
    fillColor: color,
    fillOpacity: opts.focus ? 0.18 : opts.dim ? 0.03 : 0.08,
    dashArray: opts.dim ? '4 4' : undefined,
  });
  poly.bindTooltip(`${escapeHtml(f.name)}${f.scale ? ` · ${f.scale}` : ''}`, { className: 'geo-tip', sticky: true });
  if (opts.onClick) poly.on('click', opts.onClick);
  return poly;
}

export function dot(lat: number, lon: number, color: string, radius = 4, tip?: string) {
  const m = L.circleMarker([lat, lon], { radius, color: PALETTE.markerOutline, weight: 1, fillColor: color, fillOpacity: 0.95 });
  if (tip) m.bindTooltip(tip, { className: 'geo-tip' });
  return m;
}

export function boundsOf(points: [number, number][]): L.LatLngBounds | null {
  if (!points.length) return null;
  return L.latLngBounds(points.map(p => L.latLng(p[0], p[1])));
}

export function escapeHtml(s: string): string {
  return String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]!));
}
