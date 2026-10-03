import { useEffect, useState } from 'react';
import { Link, useLocation } from 'react-router-dom';
import { RefreshCw, Truck } from 'lucide-react';
import { NAV, sectionFor } from './nav';
import { useModuleOn, useTenant } from './tenant';
import { useTheme, type ThemeName } from './theme';
import TripClassFilter from '../modules/analytics/components/layout/TripClassFilter';
import ConsignorSwitcher from '../modules/analytics/components/layout/ConsignorSwitcher';
import { useFreshness } from '../modules/geofence/lib/freshness';
import { fmtDuration, fmtInt } from '../modules/geofence/lib/format';

/**
 * The one sidebar. Sections are grouped by what you are trying to do, and
 * each owns every view of its subject (core/nav.ts); the views themselves are
 * tabs on the page, which is what keeps this list short.
 */
export default function Sidebar() {
  const { pathname } = useLocation();
  const active = sectionFor(pathname);
  const moduleOn = useModuleOn();
  const tenant = useTenant();

  return (
    <aside className="w-60 bg-panel border-r border-gray-800 flex flex-col shrink-0 h-screen sticky top-0">
      <Link to="/" className="p-4 flex items-center gap-3 border-b border-gray-800">
        <div className="w-9 h-9 bg-blue-600 rounded-lg flex items-center justify-center shrink-0">
          <Truck className="w-5 h-5 text-on-accent" />
        </div>
        <div className="min-w-0">
          <h1 className="text-base font-bold text-white leading-tight">{tenant?.branding?.product_name || 'NexGen Transport'}</h1>
          <p className="text-xs text-gray-500 truncate">{tenant?.tenant?.name ? `${tenant.tenant.name} · fleet intelligence` : 'Fleet intelligence'}</p>
        </div>
      </Link>

      <TripClassFilter />
      <ConsignorSwitcher />

      <nav className="flex-1 py-3 px-3 overflow-y-auto" aria-label="Sections">
        {NAV.map((band, bi) => {
          const sections = band.sections.filter(s => moduleOn(s.module));
          if (!sections.length) return null;
          return (
            <div key={band.label ?? bi} className="space-y-0.5">
              {band.label && (
                <p className="px-3 pt-4 pb-1.5 text-xs font-semibold uppercase tracking-wider text-gray-500">{band.label}</p>
              )}
              {sections.map(s => {
                const isActive = active?.id === s.id;
                return (
                  <Link key={s.id} to={s.path} aria-current={isActive ? 'page' : undefined}
                    className={`flex items-center gap-3 px-3 py-2 rounded-lg text-sm font-medium transition-colors ${isActive
                      ? 'bg-blue-600/10 text-blue-400 border-l-2 border-blue-500'
                      : 'text-gray-400 hover:bg-gray-800 hover:text-gray-200'}`}>
                    <s.icon className="w-[18px] h-[18px] shrink-0" />
                    <span className="truncate">{s.label}</span>
                  </Link>
                );
              })}
            </div>
          );
        })}
      </nav>

      <div className="p-4 border-t border-gray-800 text-xs text-gray-500 space-y-2">
        <SystemHealth />
        <Freshness />
        <ThemeSwitch />
      </div>
    </aside>
  );
}

/** Are all the services up? Polled from the supervisor; a click opens the developer page. */
function SystemHealth() {
  const [state, setState] = useState<{ up: number; total: number; down: string[] } | null>(null);
  const [unreachable, setUnreachable] = useState(false);

  useEffect(() => {
    let alive = true;
    const poll = () => fetch('/api/v1/dev/overview')
      .then(r => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
      .then(d => {
        if (!alive) return;
        const svcs: any[] = d.services || [];
        const ok = svcs.filter(s => s.state === 'running' && s.health_status === 'ok');
        setState({ up: ok.length, total: svcs.length, down: svcs.filter(s => !ok.includes(s)).map(s => s.title) });
        setUnreachable(false);
      })
      .catch(() => { if (alive) setUnreachable(true); });
    poll();
    const id = window.setInterval(poll, 30_000);
    return () => { alive = false; window.clearInterval(id); };
  }, []);

  const healthy = !unreachable && state && state.up === state.total;
  return (
    <Link to="/developer" className="flex items-center gap-2 hover:text-gray-300" title={state?.down.length ? `Not healthy: ${state.down.join(', ')}` : undefined}>
      <span className={`w-2 h-2 rounded-full shrink-0 ${unreachable ? 'bg-red-400' : !state ? 'bg-gray-600' : healthy ? 'bg-emerald-400' : 'bg-amber-400'}`} />
      {unreachable ? 'Gateway not answering'
        : !state ? 'Checking services…'
        : healthy ? `All ${state.total} services healthy`
        : `${state.up} of ${state.total} services healthy`}
    </Link>
  );
}

/** When the fence and route figures last moved, and when they will next (the 15-minute refresh). */
function Freshness() {
  const { status, requestRefresh, requesting } = useFreshness();
  const rf = status?.refresh;
  const run = status?.published_run;
  if (!rf && !run) return null;
  const now = Date.now();
  const at = (v: any) => (v ? new Date(String(v).replace(' ', 'T')).getTime() : null);
  const lastOk = at(rf?.last_ok);
  const next = at(rf?.next_due);
  const running = !!(rf?.running || requesting || rf?.requested);
  return (
    <div className="space-y-0.5">
      <div className="flex items-center justify-between gap-2">
        <span className="flex items-center gap-2 min-w-0">
          <span className={`w-2 h-2 rounded-full shrink-0 ${rf?.enabled ? 'bg-emerald-400' : 'bg-gray-600'} ${running ? 'animate-pulse-dot' : ''}`} />
          <span className="truncate">
            {run ? `Fence results: run #${run.i_run_id} · ${fmtInt(run.i_trips)} trips` : 'No published fence run yet'}
          </span>
        </span>
        {rf && (
          <button onClick={requestRefresh} disabled={running} title={rf.enabled ? 'Refresh now' : 'The geofence detector is stopped'}
            className="p-0.5 rounded text-gray-500 hover:text-gray-200 disabled:opacity-40 shrink-0">
            <RefreshCw className={`w-3.5 h-3.5 ${running ? 'animate-spin' : ''}`} />
          </button>
        )}
      </div>
      {running ? <p className="pl-4 text-blue-400">refreshing…</p> : lastOk ? (
        <p className="pl-4">
          updated {fmtDuration(Math.max(0, (now - lastOk) / 1000))} ago
          {next && rf?.enabled ? ` · next in ${fmtDuration(Math.max(0, (next - now) / 1000))}` : ''}
        </p>
      ) : null}
      {rf?.last?.s_status === 'failed' && <p className="pl-4 text-red-400" title={rf.last.s_error || ''}>last refresh failed</p>}
    </div>
  );
}

function ThemeSwitch() {
  const { theme, setTheme } = useTheme();
  return (
    <div className="flex items-center justify-between pt-2 border-t border-gray-800">
      <span>Theme</span>
      <div className="flex gap-0.5 bg-gray-900 border border-gray-800 rounded-md p-0.5">
        {(['teal', 'classic'] as ThemeName[]).map(t => (
          <button key={t} onClick={() => setTheme(t)}
            className={`px-2 py-0.5 rounded text-xs font-medium capitalize ${theme === t
              ? 'bg-blue-600/15 text-blue-400' : 'text-gray-500 hover:text-gray-300'}`}>
            {t}
          </button>
        ))}
      </div>
    </div>
  );
}
