import { useMemo } from 'react';
import { Link } from 'react-router-dom';
import {
  Trophy, Grid3x3, ShieldAlert, GitCompareArrows, Satellite, ArrowRight, Route,
} from 'lucide-react';
import SectionHub, { type HubCard } from '../../components/layout/SectionHub';
import { useTTAFilters } from '../../components/tta/dashboard/FilterContext';
import { useApi } from '../../hooks/useApi';
import { getReliabilityMatrix } from '../../services/transporters';
import { QUADRANT_COLORS } from '../../components/charts/QuadrantScatter';
import { formatNumber } from '../../lib/formatters';

const CARDS: HubCard[] = [
  {
    path: '/transporters/league', title: 'League Table', icon: Trophy,
    desc: 'Every carrier scored and ranked on one row each — volume, on-time, transit, detention, alerts and GPS uptime.',
    color: 'text-amber-400', gradient: 'from-amber-600/15 to-yellow-600/15', border: 'border-amber-500/20',
  },
  {
    path: '/transporters/league#matrix', title: 'Reliability Matrix', icon: Grid3x3,
    desc: 'Volume against reliability in four quadrants. Answers where the freight risk sits, which a ranked list cannot.',
    color: 'text-emerald-400', gradient: 'from-emerald-600/15 to-teal-600/15', border: 'border-emerald-500/20',
    badge: 'new',
  },
  {
    path: '/transporters/lanes', title: 'Best Carrier per Lane', icon: Route,
    desc: 'Who to give each lane to, judged only against carriers who ran the same route — the only comparison that isolates the carrier from the road.',
    color: 'text-amber-400', gradient: 'from-amber-600/15 to-orange-600/15', border: 'border-amber-500/20',
    badge: 'new',
  },
  {
    path: '/analytics/safety', title: 'Safety by Carrier', icon: ShieldAlert,
    desc: 'Speeding episodes per 100 trips, in-plant versus road, rated only over trips that actually produced GPS.',
    color: 'text-red-400', gradient: 'from-red-600/15 to-rose-600/15', border: 'border-red-500/20',
    badge: 'new',
  },
  {
    path: '/geofence', title: 'Tracking Quality', icon: Satellite,
    desc: 'Which carriers actually report GPS, ranked on a confidence interval rather than a raw percentage.',
    color: 'text-blue-400', gradient: 'from-blue-600/15 to-sky-600/15', border: 'border-blue-500/20',
  },
  {
    path: '/tta-compare', title: 'Compare Carriers', icon: GitCompareArrows,
    desc: 'Two to four carriers head to head, on the lanes they both actually run — the only fair comparison.',
    color: 'text-violet-400', gradient: 'from-violet-600/15 to-purple-600/15', border: 'border-violet-500/20',
  },
];

const QUADRANT_ORDER = ['critical', 'core', 'grow', 'review'] as const;

export default function TransporterHub() {
  const { params, paramsKey } = useTTAFilters();
  const { data: matrix } = useApi(() => getReliabilityMatrix(params as never, 10), [paramsKey]);

  // The carriers worth opening first: biggest share of freight inside the
  // quadrant that costs the most to ignore.
  const critical = useMemo(
    () => (matrix?.carriers ?? [])
      .filter(c => c.quadrant === 'critical')
      .sort((a, b) => (b.share_pct ?? 0) - (a.share_pct ?? 0))
      .slice(0, 4),
    [matrix]);

  return (
    <SectionHub
      title="🚚 Transporters"
      intro="Everything about the carriers, in one section. The filter bar above applies throughout, and every carrier name anywhere in the app opens the same full profile."
      cards={CARDS}
    >
      {matrix && (
        <>
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 mb-6">
            {QUADRANT_ORDER.map(q => {
              const s = matrix.quadrants?.[q];
              if (!s) return null;
              return (
                <Link key={q} to="/transporters/league"
                  className="bg-gray-900 border border-gray-800 rounded-lg p-3 hover:border-gray-600 transition-colors"
                  style={{ borderLeftColor: QUADRANT_COLORS[q], borderLeftWidth: 3 }}>
                  <p className="text-xs font-semibold" style={{ color: QUADRANT_COLORS[q] }}>{s.label}</p>
                  <p className="text-lg font-bold text-gray-100 mt-0.5">
                    {s.carriers}
                    <span className="text-xs text-gray-500 font-normal"> carriers · {s.share_pct ?? 0}% of freight</span>
                  </p>
                </Link>
              );
            })}
          </div>

          {!!critical.length && (
            <div className="bg-red-950/20 border border-red-900/60 rounded-xl p-4 mb-8">
              <h2 className="text-sm font-semibold text-red-300 mb-1">Open these first</h2>
              <p className="text-xs text-gray-400 mb-3">
                Carriers holding real volume that are missing dates — ordered by how much
                freight rides on them, not by how bad they look.
              </p>
              <div className="grid sm:grid-cols-2 gap-2">
                {critical.map(c => (
                  <Link key={c.name} to={`/transporters/${encodeURIComponent(c.name)}`}
                    className="flex items-center justify-between gap-3 bg-gray-900/70 border border-gray-800 rounded-lg px-3 py-2 hover:border-gray-600 transition-colors">
                    <span className="text-xs text-gray-200 truncate">{c.name}</span>
                    <span className="text-xs text-gray-500 shrink-0 flex items-center gap-2">
                      {formatNumber(c.trips)} trips · {c.share_pct}%
                      <span className="text-red-400 font-semibold">{c.otd_pct}%</span>
                      <ArrowRight className="w-3 h-3" />
                    </span>
                  </Link>
                ))}
              </div>
            </div>
          )}
        </>
      )}
    </SectionHub>
  );
}
