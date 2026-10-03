/**
 * The product's navigation: sections organised by what you are trying to do.
 *
 * The two applications NexGen was built from each had a sidebar of their own,
 * and several of their pages answer the same question from a different angle
 * -- a vehicle's trips and KPIs, and the same vehicle's fence visits and
 * alerts. Rather than two lists that both say "Vehicles", each section here
 * owns every view of its subject, and the views appear as tabs at the top of
 * the page (SectionTabs). The sidebar stays one short list.
 *
 * Paths are unchanged from the original applications, so every bookmark and
 * deep link keeps working; the geofence module's pages moved under /geo
 * because their paths collided (/trips, /vehicles, ...).
 *
 * A pattern matches its path and everything below it (`/trips` matches
 * `/trips/42`); a leading `=` makes it exact.
 */
import type { LucideIcon } from 'lucide-react';
import {
  Activity, AlertTriangle, BarChart3, BookOpen, Brain, Building2, CalendarDays, CircleDot, Cloud, Database,
  Factory, Flame, GitCompareArrows, Hexagon, Landmark, LayoutDashboard, MapPin, MapPinned, Navigation, Network,
  Route, Satellite, ShieldAlert, ShieldCheck, Terminal, Truck, Upload, UserRound, Users, Waypoints,
} from 'lucide-react';
import { ANALYTICS_LINKS, ML_LINKS, TRANSPORTER_LINKS, type SectionLink } from '../modules/analytics/lib/sectionNav';

export interface Tab {
  path: string;
  label: string;
  icon?: LucideIcon;
  /** Patterns this tab is active on; defaults to its own path (exact when `exact`). */
  match?: string[];
  exact?: boolean;
}

export interface Section {
  id: string;
  label: string;
  icon: LucideIcon;
  /** Where the sidebar entry goes. */
  path: string;
  /** Paths that belong to this section (for the sidebar highlight and its tabs). */
  match: string[];
  tabs?: Tab[];
  /** The tenant module switch (config/tenants) that shows or hides it. */
  module?: string;
  /** The page reads fleet-wide figures: the consignor / trip-class filters do not apply. */
  fleetWide?: boolean;
}

export interface Band {
  label: string | null;
  sections: Section[];
}

const fromLinks = (links: SectionLink[]): Tab[] =>
  links.map(l => ({ path: l.path, label: l.label, icon: l.icon, exact: l.exact }));

export const NAV: Band[] = [
  {
    label: null,
    sections: [
      {
        id: 'dashboard', module: 'overview', label: 'Dashboard', icon: LayoutDashboard, path: '/', match: ['=/', '=/geo', '/geo/day'],
        tabs: [
          { path: '/', label: 'Fleet dashboard', icon: LayoutDashboard, exact: true },
          { path: '/geo', label: 'Day by day', icon: CalendarDays, match: ['=/geo', '/geo/day'] },
        ],
      },
      { id: 'live', module: 'live', label: 'Live map', icon: Activity, path: '/geo/live', match: ['/geo/live'], fleetWide: true },
    ],
  },
  {
    label: 'Analyse',
    sections: [
      { id: 'analytics', module: 'analytics', label: 'Analytics', icon: BarChart3, path: '/analytics', match: ['/analytics'],
        tabs: fromLinks(ANALYTICS_LINKS) },
      {
        id: 'transporters', module: 'analytics', label: 'Transporters', icon: Truck, path: '/transporters',
        match: ['/transporters', '/geo/transporters'],
        tabs: [...fromLinks(TRANSPORTER_LINKS),
          { path: '/geo/transporters', label: 'Fence behaviour', icon: Hexagon }],
      },
      { id: 'compare', module: 'analytics', label: 'Compare & benchmark', icon: GitCompareArrows, path: '/tta-compare', match: ['/tta-compare'] },
      { id: 'ml', module: 'intelligence', label: 'ML insights', icon: Brain, path: '/ml', match: ['/ml'], tabs: fromLinks(ML_LINKS) },
    ],
  },
  {
    label: 'Operations',
    sections: [
      {
        id: 'trips', module: 'trips', label: 'Trips', icon: Satellite, path: '/trips',
        match: ['/trips', '/tta', '/geo/trips', '/geo/upload'],
        tabs: [
          { path: '/trips', label: 'All trips', icon: Satellite, match: ['=/trips', '=/tta'] },
          { path: '/geo/trips', label: 'Fence timelines', icon: MapPinned, match: ['=/geo/trips'] },
          { path: '/geo/upload', label: 'Upload & trace a trip', icon: Upload },
        ],
      },
      {
        id: 'routes', module: 'routing', label: 'Routes & lanes', icon: Route, path: '/routes',
        match: ['/routes', '/geo/routes', '/geo/lanes'],
        tabs: [
          { path: '/routes', label: 'Routes', icon: Route },
          { path: '/geo/routes', label: 'Plan vs actual & cost', icon: Navigation },
          { path: '/geo/lanes', label: 'Lanes', icon: Waypoints },
        ],
      },
      {
        id: 'vehicles', module: 'fleet', label: 'Vehicles', icon: Truck, path: '/vehicles', match: ['/vehicles', '/geo/vehicles'],
        tabs: [
          { path: '/vehicles', label: 'Fleet', icon: Truck },
          { path: '/geo/vehicles', label: 'Fence activity', icon: Hexagon },
        ],
      },
      {
        id: 'drivers', module: 'fleet', label: 'Drivers', icon: Users, path: '/drivers', match: ['/drivers', '/geo/drivers'],
        tabs: [
          { path: '/drivers', label: 'Drivers', icon: Users },
          { path: '/geo/drivers', label: 'Fence behaviour', icon: UserRound },
        ],
      },
      {
        id: 'partners', module: 'partners', label: 'Partners', icon: Network, path: '/consignors', match: ['/consignors', '/consignees'],
        tabs: [
          { path: '/consignors', label: 'Consignors', icon: Factory },
          { path: '/consignees', label: 'Consignees', icon: Building2 },
        ],
      },
    ],
  },
  {
    label: 'Tracking',
    sections: [
      {
        id: 'geofences', module: 'geofencing', label: 'Geofences', icon: Hexagon, path: '/geo/geofences',
        match: ['/geo/geofences', '/geofence', '/geo/states'],
        tabs: [
          { path: '/geo/geofences', label: 'Fence master', icon: Hexagon },
          { path: '/geofence', label: 'Detention & delivery proof', icon: ShieldCheck },
          { path: '/geo/states', label: 'States & tolls', icon: Landmark },
        ],
      },
      { id: 'alerts', module: 'geofencing', label: 'Alerts', icon: AlertTriangle, path: '/geo/alerts', match: ['/geo/alerts'], fleetWide: true },
      {
        id: 'stops', module: 'network', label: 'Stops & hotspots', icon: CircleDot, path: '/geo/stops', match: ['/geo/stops', '/tta-hotspots'],
        tabs: [
          { path: '/geo/stops', label: 'Stops & dwell', icon: CircleDot },
          { path: '/tta-hotspots', label: 'Unscheduled stops', icon: ShieldAlert },
        ],
      },
      {
        id: 'network', module: 'network', label: 'GPS network', icon: Flame, path: '/tta-network', match: ['/tta-network', '/tta-waypoints'],
        tabs: [
          { path: '/tta-network', label: 'Ping density', icon: Flame },
          { path: '/tta-waypoints', label: 'Waypoints', icon: MapPin },
        ],
      },
    ],
  },
  {
    label: 'Data',
    sections: [
      { id: 'sync', module: 'data', label: 'Data sync', icon: Cloud, path: '/etl-sync', match: ['/etl-sync'] },
      { id: 'quality', module: 'data', label: 'Data quality', icon: ShieldCheck, path: '/geo/quality', match: ['/geo/quality'], fleetWide: true },
      { id: 'migration', module: 'data', label: 'Migration', icon: Database, path: '/migration', match: ['/migration'] },
      {
        id: 'method', module: 'data', label: 'How it works', icon: BookOpen, path: '/manual', match: ['/manual', '/geo/method'],
        tabs: [
          { path: '/manual', label: 'Calculation manual', icon: BookOpen },
          { path: '/geo/method', label: 'Geofencing method', icon: Hexagon },
        ],
      },
      { id: 'developer', module: 'developer', label: 'Developer', icon: Terminal, path: '/developer', match: ['/developer'] },
    ],
  },
];

export function matches(pattern: string, path: string): boolean {
  if (pattern.startsWith('=')) return path === pattern.slice(1);
  return path === pattern || path.startsWith(pattern.endsWith('/') ? pattern : `${pattern}/`);
}

export function tabActive(tab: Tab, path: string): boolean {
  if (tab.match) return tab.match.some(p => matches(p, path));
  return tab.exact ? path === tab.path : matches(tab.path, path);
}

/** The section a path belongs to: the one with the longest matching pattern. */
export function sectionFor(path: string): Section | null {
  let best: Section | null = null;
  let bestLen = -1;
  for (const band of NAV) {
    for (const s of band.sections) {
      for (const p of s.match) {
        const len = p.replace(/^=/, '').length;
        if (matches(p, path) && len > bestLen) { best = s; bestLen = len; }
      }
    }
  }
  return best;
}

/** A trip's own pages, which share the trip workspace (core/TripTabs). */
export function tripFromPath(path: string): { tripNo: string; view: 'summary' | 'analysis' | 'fences' } | null {
  let m = path.match(/^\/(?:trips|tta)\/([^/]+)\/analysis\/?$/);
  if (m) return { tripNo: decodeURIComponent(m[1]), view: 'analysis' };
  m = path.match(/^\/(?:trips|tta)\/([^/]+)\/?$/);
  if (m) return { tripNo: decodeURIComponent(m[1]), view: 'summary' };
  m = path.match(/^\/geo\/trips\/([^/]+)\/?$/);
  if (m) return { tripNo: decodeURIComponent(m[1]), view: 'fences' };
  return null;
}

export const isFleetWidePath = (path: string) =>
  path.startsWith('/geo/') || path === '/geo' || !!sectionFor(path)?.fleetWide;
