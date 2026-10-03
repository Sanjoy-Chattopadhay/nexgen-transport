import { useSearchParams } from 'react-router-dom';
import { Handshake, Satellite } from 'lucide-react';
import PageContainer from '../components/layout/PageContainer';
import TripCompare from '../components/compare/TripCompare';
import TransporterCompare from '../components/compare/TransporterCompare';

/**
 * Compare & benchmark — two benchmarking views on one page.
 *
 * Trips answers "which run went better", transporters answers "which partner
 * runs better". Same question at two altitudes, so they share a page, a colour
 * palette (components/compare/palette.ts) and the same read: pick 2–4, see who
 * wins each metric.
 */
const TABS = [
  {
    id: 'trips', label: 'Trips', icon: Satellite,
    hint: 'Same lane, different days — or same vehicle, different drivers.',
  },
  {
    id: 'transporters', label: 'Transporters', icon: Handshake,
    hint: 'Carrier vs carrier, including the lanes they both actually run.',
  },
] as const;

type TabId = typeof TABS[number]['id'];

export default function TTACompare() {
  const [params, setParams] = useSearchParams();
  const tab: TabId = params.get('tab') === 'transporters' ? 'transporters' : 'trips';

  const select = (id: TabId) => {
    setParams(prev => {
      const next = new URLSearchParams(prev);
      next.set('tab', id);
      return next;
    }, { replace: true });
  };

  return (
    <PageContainer>
      <div className="flex items-end gap-1 border-b border-gray-800 mb-6">
        {TABS.map(t => {
          const active = tab === t.id;
          return (
            <button key={t.id} onClick={() => select(t.id)} title={t.hint}
              className={`flex items-center gap-2 px-4 py-2.5 text-sm font-medium rounded-t-lg border-b-2 -mb-px transition-colors ${
                active
                  ? 'border-blue-500 text-blue-400 bg-blue-600/10'
                  : 'border-transparent text-gray-500 hover:text-gray-300 hover:bg-gray-800/50'
              }`}>
              <t.icon className="w-4 h-4" />
              {t.label}
            </button>
          );
        })}
        <p className="ml-auto pb-2.5 text-xs text-gray-500 hidden md:block">
          {TABS.find(t => t.id === tab)?.hint}
        </p>
      </div>

      {/*
        Both panels stay mounted and the inactive one is hidden with CSS, so a
        comparison that took a while to run survives a tab switch. Mounting is
        deliberately NOT conditional on which tab has been visited: tying mount
        state to the tab made it possible for that state to fall out of step
        with the URL (back button, deep link, replace-navigation) and render the
        wrong panel. The cost is one extra list request per page load.
      */}
      <div className={tab === 'trips' ? '' : 'hidden'}><TripCompare /></div>
      <div className={tab === 'transporters' ? '' : 'hidden'}><TransporterCompare /></div>
    </PageContainer>
  );
}
