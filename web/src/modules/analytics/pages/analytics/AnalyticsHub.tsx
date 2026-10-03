import {
  Factory, TrendingUp, Truck, ShieldAlert, Route, Map as MapIcon,
  Flame, Gauge, BarChart3, Search,
} from 'lucide-react';
import SectionHub, { type HubCard } from '../../components/layout/SectionHub';
import { useTTAFilters } from '../../components/tta/dashboard/FilterContext';
import { useApi } from '../../hooks/useApi';
import { getDashKpis } from '../../services/ttaDashboard';
import { formatNumber } from '../../lib/formatters';

const CARDS: HubCard[] = [
  {
    path: '/analytics/overview', title: 'Executive Overview', icon: Factory,
    desc: 'What needs attention right now, ranked by what it costs to ignore — then the first rows of every table below.',
    color: 'text-blue-400', gradient: 'from-blue-600/15 to-indigo-600/15', border: 'border-blue-500/20',
    group: 'Start here',
  },
  {
    path: '/analytics/trends', title: 'Time & Trends', icon: TrendingUp,
    desc: 'Is service getting better or worse? Volume, on-time and transit over time, daily through monthly.',
    color: 'text-cyan-400', gradient: 'from-cyan-600/15 to-sky-600/15', border: 'border-cyan-500/20',
    group: 'Start here',
  },
  {
    path: '/analytics/safety', title: 'Speed & Safety', icon: ShieldAlert,
    desc: 'How fast trucks run inside a plant versus on the road, against a limit you choose, plus violations per day.',
    color: 'text-red-400', gradient: 'from-red-600/15 to-rose-600/15', border: 'border-red-500/20',
    group: 'Start here', badge: 'new',
  },
  {
    path: '/analytics/lanes', title: 'Routes & Lanes', icon: Route,
    desc: 'Which lanes lose time, and whether they miss the ETA that was quoted for them.',
    color: 'text-emerald-400', gradient: 'from-emerald-600/15 to-teal-600/15', border: 'border-emerald-500/20',
    group: 'Where the freight goes',
  },
  {
    path: '/analytics/geo', title: 'Geo Map', icon: MapIcon,
    desc: 'Volume and on-time by state, on two bubble maps — where the freight goes, and where it lands late.',
    color: 'text-sky-400', gradient: 'from-sky-600/15 to-blue-600/15', border: 'border-sky-500/20',
    group: 'Where the freight goes', badge: 'new',
  },
  {
    path: '/analytics/fleet', title: 'Fleet & Vehicles', icon: Gauge,
    desc: 'Own versus market trucks, vehicle categories, device types, and the vehicles whose trackers are worst.',
    color: 'text-amber-400', gradient: 'from-amber-600/15 to-orange-600/15', border: 'border-amber-500/20',
    group: 'Where the freight goes',
  },
  {
    path: '/analytics/heatmaps', title: 'Heatmaps & Correlations', icon: Flame,
    desc: 'When trucks actually leave the plant, which carriers drifted month to month, and which metric moves which.',
    color: 'text-orange-400', gradient: 'from-orange-600/15 to-red-600/15', border: 'border-orange-500/20',
    group: 'Dig deeper',
  },
  {
    path: '/analytics/distributions', title: 'Distributions', icon: BarChart3,
    desc: 'The spread behind an average — because a mean transit time hides the tail that actually hurts.',
    color: 'text-violet-400', gradient: 'from-violet-600/15 to-purple-600/15', border: 'border-violet-500/20',
    group: 'Dig deeper',
  },
  {
    path: '/analytics/explorer', title: 'Data Explorer', icon: Search,
    desc: 'The raw filtered trip rows behind everything above, exportable to CSV.',
    color: 'text-gray-300', gradient: 'from-gray-600/15 to-slate-600/15', border: 'border-gray-500/20',
    group: 'Dig deeper',
  },
  {
    path: '/transporters', title: 'Transporters', icon: Truck,
    desc: 'The carrier section: league table, reliability matrix and a full profile per carrier.',
    color: 'text-purple-400', gradient: 'from-purple-600/15 to-fuchsia-600/15', border: 'border-purple-500/20',
    group: 'Dig deeper',
  },
];

export default function AnalyticsHub() {
  const { params, paramsKey } = useTTAFilters();
  const { data: kpi } = useApi(() => getDashKpis(params), [paramsKey]);
  const cur = kpi?.current ?? {};

  return (
    <SectionHub
      title="📊 TTA Analytics"
      intro="Every page below reads the same filtered trip set — the date window, consignor and dimension filters in the bar above apply to all of them, so a figure here and the same figure there always describe the same trips."
      cards={CARDS}
    >
      <div className="grid grid-cols-2 md:grid-cols-5 gap-3 mb-8">
        {[
          ['Trips in filter', formatNumber(cur.trips)],
          ['On-time', cur.otd_pct != null ? `${cur.otd_pct}%` : '—'],
          ['Avg transit', cur.avg_transit_hours != null ? `${cur.avg_transit_hours} h` : '—'],
          ['Carriers', formatNumber(cur.transporters)],
          ['Vehicles', formatNumber(cur.vehicles)],
        ].map(([l, v]) => (
          <div key={l as string} className="bg-gray-900 border border-gray-800 rounded-lg px-3 py-2.5">
            <p className="text-xs text-gray-500">{l as string}</p>
            <p className="text-lg font-bold text-gray-100">{v as string}</p>
          </div>
        ))}
      </div>
    </SectionHub>
  );
}
