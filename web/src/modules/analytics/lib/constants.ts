import type { LucideIcon } from 'lucide-react';
import { LayoutDashboard, Users, MapPin, Route, Truck, Brain, Database, Satellite, Flame, GitCompareArrows, BarChart3, Cloud, ShieldAlert, Network, SatelliteDish, BookOpen
} from 'lucide-react';

/**
 * The sidebar, grouped by what you are trying to DO rather than by which
 * subsystem produced the page.
 *
 * `section` starts a labelled band. Fifteen flat entries gave no clue which
 * pages belonged together, and two of them ("GPS Network", "Geofence & GPS")
 * both read as "the GPS one" — you could not tell from the labels that one is a
 * density map of pings and the other is fences, detention and tracking quality.
 * They are renamed for what they show, not for the technology behind them.
 */
/** A band header, or a destination. Discriminated so the sidebar can narrow. */
export type NavEntry =
  | { section: string; path?: undefined; label?: undefined; icon?: undefined }
  | { section?: undefined; path: string; label: string; icon: LucideIcon };

export const NAV_ITEMS: NavEntry[] = [
  { path: '/', label: 'Dashboard', icon: LayoutDashboard },

  { section: 'Analyse' },
  { path: '/analytics', label: 'TTA Analytics', icon: BarChart3 },
  { path: '/transporters', label: 'Transporters', icon: Truck },
  { path: '/ml', label: 'ML Insights', icon: Brain },
  { path: '/tta-compare', label: 'Compare & Benchmark', icon: GitCompareArrows },

  { section: 'Operations' },
  { path: '/trips', label: 'Trips', icon: Satellite },
  { path: '/drivers', label: 'Drivers', icon: Users },
  { path: '/vehicles', label: 'Vehicles', icon: Truck },
  { path: '/routes', label: 'Routes', icon: Route },
  // Collapsible group: expands to Consignors + Consignees. `/partners` is a
  // label, not a route -- Sidebar renders it as a group, never as a link.
  { path: '/partners', label: 'Partners', icon: Network },

  { section: 'Tracking' },
  // Was "Geofence & GPS": fences, works detention, tracking quality, delivery proof.
  { path: '/geofence', label: 'Geofence & Detention', icon: SatelliteDish },
  // Was "GPS Network": a country-wide density map of pings and standstill time.
  { path: '/tta-network', label: 'Ping Density Map', icon: Flame },
  { path: '/tta-waypoints', label: 'Waypoint Analysis', icon: MapPin },
  // Was "Pilferage Hotspots": it finds unscheduled STOPPING, not theft, and the
  // old name promised a conclusion the data cannot support.
  { path: '/tta-hotspots', label: 'Unscheduled Stops', icon: ShieldAlert },

  { section: 'Data' },
  { path: '/etl-sync', label: 'ETL Sync', icon: Cloud },
  { path: '/migration', label: 'Migration', icon: Database },
  // How every figure is produced, traced from the API JSON. Sits under Data
  // rather than in a help menu on purpose: it is a view of this dataset, with
  // a worked example computed from the same trips the dashboards read, and it
  // answers "is this number right?" — which is a data question.
  { path: '/manual', label: 'Calculation Manual', icon: BookOpen },
];

export const SEVERITY_STYLES: Record<string, string> = {
  critical: 'bg-red-900/40 text-red-400 border border-red-800',
  high: 'bg-red-900/40 text-red-400 border border-red-800',
  warning: 'bg-amber-900/40 text-amber-400 border border-amber-800',
  medium: 'bg-amber-900/40 text-amber-400 border border-amber-800',
  info: 'bg-blue-900/40 text-blue-400 border border-blue-800',
  low: 'bg-emerald-900/40 text-emerald-400 border border-emerald-800',
};
