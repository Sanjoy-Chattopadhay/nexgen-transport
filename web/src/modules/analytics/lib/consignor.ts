/**
 * Route-based consignor scoping (no auth).
 *
 * A consignor is carried in the URL as `/<prefix>/<id>/...` (prefix defaults to
 * "consignor", configurable via VITE_CONSIGNOR_PREFIX). When present, the app
 * mounts the router under that basename so every existing link/route stays
 * unchanged, and the API layer appends `?<param>=<id>` to every backend call.
 *
 * Absent  -> all consignors (current behaviour, fully backward compatible).
 */

export const CONSIGNOR_PREFIX: string = import.meta.env.VITE_CONSIGNOR_PREFIX || 'consignor';
export const CONSIGNOR_PARAM: string = import.meta.env.VITE_CONSIGNOR_PARAM || 'consignor_id';

/**
 * Subfolder the bundle is served from, baked in by Vite's `base` at build time
 * ('' for an origin-root deploy, '/analytics' for `BASE_PATH=/analytics/`).
 * Every path this module produces is prefixed with it so the router mounts —
 * and consignor switching navigates — inside the deployed folder.
 */
export const APP_BASE: string = (import.meta.env.BASE_URL || '/').replace(/\/+$/, '');

/** Strip the deploy subfolder off a pathname, leaving the in-app path. */
function stripAppBase(pathname: string): string {
  if (APP_BASE && (pathname === APP_BASE || pathname.startsWith(`${APP_BASE}/`))) {
    return pathname.slice(APP_BASE.length) || '/';
  }
  return pathname;
}

export interface ParsedConsignorPath {
  consignorId: string | null;
  /**
   * react-router basename, always including the deploy subfolder:
   * `/consignor/540` when scoped (`/analytics/consignor/540` under a base), else
   * the base itself (`/` at the origin root).
   */
  basename: string;
}

/** Extract the consignor scope from a pathname (defaults to the live URL). */
export function parseConsignorPath(pathname: string = window.location.pathname): ParsedConsignorPath {
  const segs = stripAppBase(pathname).replace(/^\/+/, '').split('/');
  if (segs[0] === CONSIGNOR_PREFIX && /^\d+$/.test(segs[1] || '')) {
    return { consignorId: segs[1], basename: `${APP_BASE}/${CONSIGNOR_PREFIX}/${segs[1]}` };
  }
  return { consignorId: null, basename: APP_BASE || '/' };
}

/**
 * Build the absolute URL to view the current in-app location under a different
 * consignor (or none). Switching consignor changes the router basename, so this
 * is applied via a full navigation rather than client-side routing.
 */
export function consignorUrl(id: number | string | null): string {
  const { basename } = parseConsignorPath();
  let sub = window.location.pathname;
  if (basename !== '/' && sub.startsWith(basename)) {
    sub = sub.slice(basename.length) || '/';
  }
  if (!sub.startsWith('/')) sub = '/' + sub;
  const base = id ? `${APP_BASE}/${CONSIGNOR_PREFIX}/${id}` : APP_BASE;
  const path = sub === '/' ? (base || '/') : `${base}${sub}`;
  return path + window.location.search;
}
