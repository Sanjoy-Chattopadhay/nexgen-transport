import { Link, useLocation } from 'react-router-dom';
import { sectionFor, tabActive } from './nav';

/**
 * The views inside the current section, as a tab strip at the top of the page.
 *
 * This is how a section owns pages from both modules without the sidebar
 * growing: Vehicles has the fleet view and the fence-activity view, Trips has
 * the trip list, the fence timelines and the upload tracer, and so on.
 */
export default function SectionTabs() {
  const { pathname } = useLocation();
  const section = sectionFor(pathname);
  if (!section?.tabs || section.tabs.length < 2) return null;

  return (
    <nav aria-label={`${section.label} views`}
      className="flex gap-1 overflow-x-auto border-b border-gray-800 mb-5 -mt-1">
      {section.tabs.map(t => {
        const active = tabActive(t, pathname);
        return (
          <Link key={t.path} to={t.path} aria-current={active ? 'page' : undefined}
            className={`flex items-center gap-1.5 px-3 py-2 text-sm font-medium whitespace-nowrap border-b-2 -mb-px transition-colors ${
              active ? 'border-blue-500 text-blue-400' : 'border-transparent text-gray-400 hover:text-gray-200'}`}>
            {t.icon && <t.icon className="w-4 h-4" />}
            {t.label}
          </Link>
        );
      })}
    </nav>
  );
}
