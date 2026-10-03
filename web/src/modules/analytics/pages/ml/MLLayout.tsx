import { Outlet } from 'react-router-dom';

/** Shell for the ML section. The in-section switcher between models is drawn
 *  by the app shell (core/SectionTabs), above every model page. */
export default function MLLayout() {
  return <Outlet />;
}
