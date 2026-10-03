import {
  Factory, TrendingUp, Truck, ShieldAlert, Route, Map as MapIcon, Flame, Gauge,
  BarChart3, Search, Grid3X3, Trophy, GitCompareArrows, Brain, Clock, ShieldCheck,
  AlertTriangle, Users, Building2, Settings,
} from 'lucide-react';
import type { LucideIcon } from 'lucide-react';

export interface SectionLink {
  path: string;
  label: string;
  icon: LucideIcon;
  exact?: boolean;
}

/**
 * The pages inside each section.
 *
 * These used to be sidebar trees. Three sections expanding into ten-plus
 * children each turned the sidebar into a wall of two-word labels, so the
 * sidebar now shows only the three section headers and this list drives two
 * things instead: the cards on the section's hub page, which have room to say
 * what a report is FOR, and a compact tab strip on the pages themselves so
 * moving between siblings does not mean going back to the hub.
 */
export const ANALYTICS_LINKS: SectionLink[] = [
  { path: '/analytics', label: 'All reports', icon: Grid3X3, exact: true },
  { path: '/analytics/overview', label: 'Overview', icon: Factory },
  { path: '/analytics/trends', label: 'Time & Trends', icon: TrendingUp },
  { path: '/analytics/safety', label: 'Speed & Safety', icon: ShieldAlert },
  { path: '/analytics/lanes', label: 'Routes & Lanes', icon: Route },
  { path: '/analytics/geo', label: 'Geo Map', icon: MapIcon },
  { path: '/analytics/heatmaps', label: 'Heatmaps', icon: Flame },
  { path: '/analytics/fleet', label: 'Fleet', icon: Gauge },
  { path: '/analytics/distributions', label: 'Distributions', icon: BarChart3 },
  { path: '/analytics/explorer', label: 'Explorer', icon: Search },
];

export const TRANSPORTER_LINKS: SectionLink[] = [
  { path: '/transporters', label: 'All carrier views', icon: Grid3X3, exact: true },
  { path: '/transporters/league', label: 'League & Matrix', icon: Trophy },
  { path: '/transporters/lanes', label: 'Best per Lane', icon: Route },
  { path: '/analytics/safety', label: 'Safety by Carrier', icon: ShieldAlert },
  { path: '/tta-compare', label: 'Compare Carriers', icon: GitCompareArrows },
];

export const ML_LINKS: SectionLink[] = [
  { path: '/ml', label: 'All models', icon: Grid3X3, exact: true },
  { path: '/ml/eta', label: 'ETA', icon: Clock },
  { path: '/ml/sla', label: 'SLA', icon: ShieldCheck },
  { path: '/ml/anomaly', label: 'Anomaly', icon: AlertTriangle },
  { path: '/ml/driver-scorer', label: 'Driver Scorer', icon: Gauge },
  { path: '/ml/fatigue', label: 'Fatigue', icon: Brain },
  { path: '/ml/recommender', label: 'Recommender', icon: Users },
  { path: '/ml/demand', label: 'Demand', icon: TrendingUp },
  { path: '/ml/route-optimizer', label: 'Route Optimizer', icon: Route },
  { path: '/ml/client-forecast', label: 'Client Forecast', icon: Building2 },
  { path: '/ml/models', label: 'Registry', icon: Settings },
];

/** Which section a path belongs to, for the tab strip. */
export function linksForPath(pathname: string): SectionLink[] | null {
  if (pathname.startsWith('/transporters')) return TRANSPORTER_LINKS;
  if (pathname.startsWith('/analytics')) return ANALYTICS_LINKS;
  if (pathname.startsWith('/ml')) return ML_LINKS;
  return null;
}

export const SECTION_ICONS = { Truck, BarChart3, Brain };
