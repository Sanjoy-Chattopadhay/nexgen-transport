import { Outlet } from 'react-router-dom';
import { TTAFilterProvider } from '../../components/tta/dashboard/FilterContext';
import FilterBar from '../../components/tta/dashboard/FilterBar';

/** Shared shell for all TTA analytics pages: one global filter state,
 *  one filter bar, filters affect every figure on every page. The section's
 *  page switcher is drawn by the app shell (core/SectionTabs). */
export default function AnalyticsLayout() {
  return (
    <TTAFilterProvider>
      <FilterBar />
      <Outlet />
    </TTAFilterProvider>
  );
}
