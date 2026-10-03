import { useCallback, useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { Boxes, Database, FileText, History, Pause, Play, Radio, Settings, Terminal } from 'lucide-react';
import { dev, type Overview } from './api';
import { Badge, ErrorNote, Spinner } from '../ui';
import { fmtAgo, fmtUptime } from './format';
import ServicesPanel from './ServicesPanel';
import JobsPanel from './JobsPanel';
import EventsPanel from './EventsPanel';
import LogsPanel from './LogsPanel';
import DatabasePanel from './DatabasePanel';
import ConfigPanel from './ConfigPanel';

const TABS = [
  { id: 'services', label: 'Services', icon: Boxes },
  { id: 'jobs', label: 'Jobs', icon: History },
  { id: 'events', label: 'Events', icon: Radio },
  { id: 'logs', label: 'Logs', icon: FileText },
  { id: 'database', label: 'Database', icon: Database },
  { id: 'config', label: 'Configuration', icon: Settings },
] as const;

type TabId = typeof TABS[number]['id'];

const POLL_MS = 5000;

/**
 * Every module of NexGen Transport in one place: its health, start / stop /
 * restart for each service and for each of its roles, the jobs and event
 * consumers inside them, their logs, the database and the configuration.
 *
 * Answered by the supervisor (/api/v1/dev), not by a service, so it works
 * with every service stopped.
 */
export default function DeveloperPage() {
  const [params, setParams] = useSearchParams();
  const tab = (TABS.find(t => t.id === params.get('tab'))?.id ?? 'services') as TabId;
  const logService = params.get('service') || 'supervisor';
  const [overview, setOverview] = useState<Overview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [live, setLive] = useState(true);

  const refresh = useCallback(async () => {
    try {
      setOverview(await dev.overview());
      setError(null);
    } catch (e: any) {
      setError(e?.status === undefined
        ? 'The gateway did not answer. Is NexGen running? Start it with run.bat (or python -m nexgen run).'
        : e?.message || String(e));
    }
  }, []);

  useEffect(() => { void refresh(); }, [refresh]);
  useEffect(() => {
    if (!live || tab !== 'services') return;
    const id = window.setInterval(() => { void refresh(); }, POLL_MS);
    return () => window.clearInterval(id);
  }, [live, tab, refresh]);

  const go = (id: TabId, extra: Record<string, string> = {}) => setParams({ tab: id, ...extra }, { replace: false });

  return (
    <div className="space-y-5 max-w-[1600px]">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold text-white flex items-center gap-2">
            <Terminal className="w-6 h-6 text-blue-400" /> Developer
          </h1>
          <p className="text-sm text-gray-400 mt-1 max-w-3xl">
            Every module of NexGen Transport: health, start and stop for each service and each of its workers,
            scheduled jobs, the event bus, logs, the database and the configuration.
          </p>
        </div>
        {overview && (
          <div className="flex flex-wrap items-center gap-2 text-sm">
            <Badge variant="info" title={`pid ${overview.gateway.pid}`}>
              gateway :{overview.gateway.port} · up {fmtUptime((Date.now() - new Date(overview.gateway.started_at).getTime()) / 1000)}
            </Badge>
            <Badge variant={overview.database.reachable ? 'success' : 'danger'} title={overview.database.error}>
              {overview.database.reachable ? `MySQL ${overview.database.version}` : 'database unreachable'}
            </Badge>
            <Badge title="Login is off for now; every request acts as the default tenant">auth: {overview.auth}</Badge>
            {tab === 'services' && (
              <button onClick={() => setLive(l => !l)}
                className="flex items-center gap-1 px-2 py-0.5 rounded-full border border-gray-700 text-gray-400 hover:text-gray-200 text-xs"
                title={live ? 'Pause the 5-second refresh' : 'Refresh every 5 seconds'}>
                {live ? <Pause className="w-3 h-3" /> : <Play className="w-3 h-3" />}
                {live ? `live · ${fmtAgo(overview.generated_at)}` : 'paused'}
              </button>
            )}
          </div>
        )}
      </header>

      <nav className="flex gap-1 overflow-x-auto border-b border-gray-800">
        {TABS.map(t => (
          <button key={t.id} onClick={() => go(t.id, t.id === 'logs' ? { service: logService } : {})}
            className={`flex items-center gap-1.5 px-3 py-2 text-sm font-medium whitespace-nowrap border-b-2 -mb-px transition-colors ${
              tab === t.id ? 'border-blue-500 text-blue-400' : 'border-transparent text-gray-400 hover:text-gray-200'}`}>
            <t.icon className="w-4 h-4" /> {t.label}
          </button>
        ))}
      </nav>

      {error && <ErrorNote>{error}</ErrorNote>}
      {!overview ? (!error && <Spinner />) : (
        <>
          {tab === 'services' && (
            <ServicesPanel overview={overview} refresh={refresh} onLogs={name => go('logs', { service: name })} />
          )}
          {tab === 'jobs' && <JobsPanel overview={overview} />}
          {tab === 'events' && <EventsPanel />}
          {tab === 'logs' && (
            <LogsPanel overview={overview} service={logService} setService={name => go('logs', { service: name })} />
          )}
          {tab === 'database' && <DatabasePanel />}
          {tab === 'config' && <ConfigPanel />}
        </>
      )}
    </div>
  );
}
