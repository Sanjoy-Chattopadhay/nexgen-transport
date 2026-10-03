import { useState } from 'react';
import {
  Activity, ChevronDown, ChevronRight, FileText, Play, Power, RotateCw, SkipForward, Square, Stethoscope, Zap,
} from 'lucide-react';
import { dev, type ConsumerInfo, type JobInfo, type Overview, type RoleInfo, type ServiceInfo } from './api';
import { Badge, Button, Stat, type BadgeVariant } from '../ui';
import { fmtAgo, fmtInt, fmtSeconds, fmtUptime, fmtWhen } from './format';

const STATE: Record<string, { badge: BadgeVariant; dot: string }> = {
  running: { badge: 'success', dot: 'bg-emerald-400' },
  starting: { badge: 'info', dot: 'bg-blue-400 animate-pulse-dot' },
  stopping: { badge: 'warning', dot: 'bg-amber-400 animate-pulse-dot' },
  stopped: { badge: 'neutral', dot: 'bg-gray-600' },
  crashed: { badge: 'danger', dot: 'bg-red-400' },
};

/** What stopping a service means, in the operator's terms. */
const STOP_EFFECT: Record<string, string> = {
  ingestion: 'No data will be pulled from the TMS API, and uploads will not be accepted, until it is started again.',
  fleet: 'Landed data will wait unprocessed (nothing is lost) and the fleet API pages will be unavailable.',
  geofence: 'Fence detection, the live map and the geofence pages pause; new trips wait to be evaluated.',
  routing: 'Route plans and deviations stop updating; the routes pages will be unavailable.',
  analytics: 'Dashboards, reports and KPI refreshes pause; most pages will be unavailable.',
  ml: 'Predictions and model pages will be unavailable; scheduled retraining pauses.',
  platform: 'Tenant settings, backups and maintenance will be unavailable.',
};

type Act = (key: string, fn: () => Promise<unknown>, done: string, confirm?: string) => Promise<void>;

export default function ServicesPanel({ overview, refresh, onLogs }: {
  overview: Overview; refresh: () => Promise<void>; onLogs: (service: string) => void;
}) {
  const [busy, setBusy] = useState<string | null>(null);
  const [message, setMessage] = useState<{ kind: 'ok' | 'error'; text: string } | null>(null);

  const act: Act = async (key, fn, done, confirmText) => {
    if (confirmText && !window.confirm(confirmText)) return;
    setBusy(key);
    setMessage(null);
    try {
      await fn();
      setMessage({ kind: 'ok', text: done });
    } catch (e: any) {
      setMessage({ kind: 'error', text: e?.message || String(e) });
    } finally {
      setBusy(null);
      await refresh();
    }
  };

  const services = overview.services;
  const count = (st: string) => services.filter(s => s.state === st).length;
  const running = count('running');

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap items-center gap-2 text-sm">
          <Badge variant={running === services.length ? 'success' : 'warning'}>
            {running} of {services.length} running
          </Badge>
          {count('crashed') > 0 && <Badge variant="danger">{count('crashed')} crashed</Badge>}
          {count('stopped') > 0 && <Badge>{count('stopped')} stopped</Badge>}
          {count('starting') > 0 && <Badge variant="info">{count('starting')} starting</Badge>}
        </div>
        <div className="flex gap-2">
          <Button icon={Play} busy={busy === 'all:start'} disabled={!!busy}
            onClick={() => act('all:start', dev.startAll, 'Every service was asked to start.')}>
            Start all
          </Button>
          <Button icon={Square} variant="danger" busy={busy === 'all:stop'} disabled={!!busy}
            onClick={() => act('all:stop', dev.stopAll, 'Every service was stopped.',
              'Stop every service? The web app stays up (this page keeps working), but no data will be pulled, processed or served until you start them again.')}>
            Stop all
          </Button>
        </div>
      </div>

      {message && (
        <div className={`rounded-lg border px-4 py-2.5 text-sm ${message.kind === 'ok'
          ? 'border-emerald-900 bg-emerald-950/30 text-emerald-300' : 'border-red-900 bg-red-950/30 text-red-300'}`}>
          {message.text}
        </div>
      )}

      <div className="grid grid-cols-1 2xl:grid-cols-2 gap-4">
        {services.map(s => (
          <ServiceCard key={s.name} s={s} busy={busy} act={act} onLogs={onLogs} />
        ))}
      </div>
    </div>
  );
}

function ServiceCard({ s, busy, act, onLogs }: { s: ServiceInfo; busy: string | null; act: Act; onLogs: (n: string) => void }) {
  const [ready, setReady] = useState<{ ready: boolean; checks: Record<string, unknown> } | null>(null);
  const st = STATE[s.state] ?? STATE.stopped;
  const alive = s.state === 'running' || s.state === 'starting';
  const healthOk = s.state === 'running' && s.health_status === 'ok';
  const roleNames = Object.keys(s.roles ?? {}).length
    ? Object.keys(s.roles)
    : s.declared_roles.map(r => r.name);
  const k = (a: string) => `${s.name}:${a}`;

  const checkReady = async () => {
    try {
      setReady(await dev.ready(s.name));
    } catch (e: any) {
      setReady({ ready: false, checks: { error: e?.message || String(e) } });
    }
  };

  return (
    <section className={`bg-gray-900 rounded-xl border p-5 ${s.state === 'crashed' ? 'border-red-900' : 'border-gray-800'}`}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="flex items-center gap-2 text-base font-semibold text-white">
            <span className={`w-2.5 h-2.5 rounded-full shrink-0 ${st.dot}`} />
            {s.title}
            <span className="text-sm font-normal text-gray-500">{s.name} · :{s.port}</span>
          </h3>
          <p className="text-sm text-gray-400 mt-1">{s.description}</p>
        </div>
        <div className="flex items-center gap-1.5 shrink-0">
          <Badge variant={st.badge}>{s.state}</Badge>
          {s.state === 'running' && (
            <Badge variant={healthOk ? 'success' : 'warning'} title={s.health_error || undefined}>
              {healthOk ? 'healthy' : s.health_status || 'no answer'}
            </Badge>
          )}
          {s.desired !== (alive ? 'running' : 'stopped') && (
            <Badge variant="info" title="What the supervisor is working towards">wants {s.desired}</Badge>
          )}
        </div>
      </div>

      <div className="grid grid-cols-3 sm:grid-cols-6 gap-3 mt-4">
        <Stat label="PID" value={s.pid ?? '—'} />
        <Stat label="Uptime" value={fmtUptime(s.uptime_s)} title={fmtWhen(s.started_at)} />
        <Stat label="Memory" value={s.memory_mb != null ? `${s.memory_mb.toFixed(0)} MB` : '—'} />
        <Stat label="CPU" value={s.cpu_percent != null ? `${s.cpu_percent.toFixed(0)}%` : '—'} />
        <Stat label="Restarts (5 min)" value={s.restarts_recent} />
        <Stat label="Last exit" value={s.last_exit_code ?? '—'} title={s.last_exit_at ? fmtWhen(s.last_exit_at) : undefined} />
      </div>

      {s.health_error && s.state !== 'stopped' && (
        <p className="mt-3 text-sm text-amber-300 break-words">{s.health_error}</p>
      )}

      <div className="flex flex-wrap gap-2 mt-4">
        {!alive ? (
          <Button icon={Play} variant="primary" busy={busy === k('start')} disabled={!!busy}
            onClick={() => act(k('start'), () => dev.service(s.name, 'start'), `${s.title} started.`)}>
            Start
          </Button>
        ) : (
          <Button icon={Square} variant="danger" busy={busy === k('stop')} disabled={!!busy}
            onClick={() => act(k('stop'), () => dev.service(s.name, 'stop'), `${s.title} stopped.`,
              `Stop ${s.title}? ${STOP_EFFECT[s.name] ?? 'Its pages and jobs pause until it is started again.'}`)}>
            Stop
          </Button>
        )}
        <Button icon={RotateCw} busy={busy === k('restart')} disabled={!!busy}
          onClick={() => act(k('restart'), () => dev.service(s.name, 'restart'), `${s.title} restarted.`,
            `Restart ${s.title}? It is unavailable for a few seconds; work in progress is retried.`)}>
          Restart
        </Button>
        <Button icon={FileText} onClick={() => onLogs(s.name)}>Logs</Button>
        <Button icon={Stethoscope} disabled={!alive} onClick={checkReady}
          title="Ask the service whether everything it depends on answers">Check readiness</Button>
      </div>

      {ready && (
        <div className={`mt-3 rounded-lg border px-3 py-2 text-sm ${ready.ready
          ? 'border-emerald-900 bg-emerald-950/20 text-emerald-300' : 'border-amber-900 bg-amber-950/20 text-amber-300'}`}>
          <p className="font-medium">{ready.ready ? 'Ready' : 'Not ready'}</p>
          <ul className="mt-1 space-y-0.5 text-gray-300">
            {Object.entries(ready.checks ?? {}).map(([name, v]) => (
              <li key={name} className="break-words"><span className="text-gray-500">{name}:</span> {String(typeof v === 'object' ? JSON.stringify(v) : v)}</li>
            ))}
          </ul>
        </div>
      )}

      <div className="mt-4 space-y-2">
        {roleNames.map(name => (
          <RoleRow key={name} service={s} name={name} role={s.roles?.[name]} serviceAlive={s.state === 'running'}
            busy={busy} act={act} />
        ))}
      </div>

      <details className="mt-3 text-sm text-gray-500">
        <summary className="cursor-pointer hover:text-gray-300">Routes this service answers ({s.routes.length})</summary>
        <ul className="mt-2 grid grid-cols-1 md:grid-cols-2 gap-x-4 gap-y-0.5 font-mono text-xs text-gray-400">
          {s.routes.map(r => <li key={r} className="truncate" title={r}>{r}</li>)}
        </ul>
      </details>
    </section>
  );
}

function RoleRow({ service, name, role, serviceAlive, busy, act }: {
  service: ServiceInfo; name: string; role?: RoleInfo; serviceAlive: boolean; busy: string | null; act: Act;
}) {
  const declared = service.declared_roles.find(r => r.name === name);
  const isWorker = role ? role.kind === 'worker' : name !== 'api';
  const hasDetail = !!role && isWorker && ((role.consumers?.length ?? 0) + (role.jobs?.length ?? 0) + (role.loops?.length ?? 0) > 0);
  const [open, setOpen] = useState(false);
  const running = !!role?.running && serviceAlive;
  const k = (a: string) => `${service.name}:${name}:${a}`;
  const description = role?.description || declared?.description || (name === 'api' ? 'public API' : '');

  return (
    <div className="rounded-lg border border-gray-800 bg-gray-950/40">
      <div className="flex flex-wrap items-center gap-2 px-3 py-2">
        <button onClick={() => setOpen(o => !o)} disabled={!hasDetail}
          className="flex items-center gap-2 min-w-0 flex-1 text-left disabled:cursor-default">
          {hasDetail ? (open ? <ChevronDown className="w-4 h-4 text-gray-500 shrink-0" /> : <ChevronRight className="w-4 h-4 text-gray-500 shrink-0" />)
            : <span className="w-4 shrink-0" />}
          <span className={`w-2 h-2 rounded-full shrink-0 ${running ? 'bg-emerald-400' : 'bg-gray-600'}`} />
          <span className="text-sm font-medium text-gray-200">{name}</span>
          <Badge variant={isWorker ? 'info' : 'neutral'}>{isWorker ? 'worker' : 'api'}</Badge>
          <span className="text-sm text-gray-500 truncate">{description}</span>
        </button>
        {role?.last_error && <Badge variant="danger" title={role.last_error}>error</Badge>}
        {serviceAlive && (role?.running ? (
          <Button small icon={Power} variant="danger" busy={busy === k('stop')} disabled={!!busy}
            onClick={() => act(k('stop'), () => dev.role(service.name, name, 'stop'), `${service.title}: ${name} stopped.`,
              name === 'api'
                ? `Stop ${service.title}'s API? Its pages answer "service stopped" until it is started again; its workers keep running.`
                : `Stop the ${name} worker of ${service.title}? Its jobs and event consumers pause; events wait, nothing is lost.`)}>
            Stop
          </Button>
        ) : (
          <Button small icon={Play} busy={busy === k('start')} disabled={!!busy}
            onClick={() => act(k('start'), () => dev.role(service.name, name, 'start'), `${service.title}: ${name} started.`)}>
            Start
          </Button>
        ))}
      </div>
      {role?.last_error && (
        <p className="px-3 pb-2 text-sm text-red-300 break-words">{role.last_error}</p>
      )}
      {open && role && (
        <div className="border-t border-gray-800 px-3 py-3 space-y-3">
          {!!role.consumers?.length && (
            <div>
              <p className="text-xs uppercase tracking-wide text-gray-500 mb-1.5">Event consumers</p>
              <div className="space-y-1.5">
                {role.consumers.map(c => <ConsumerRowView key={c.name} service={service} c={c} busy={busy} act={act} />)}
              </div>
            </div>
          )}
          {!!role.jobs?.length && (
            <div>
              <p className="text-xs uppercase tracking-wide text-gray-500 mb-1.5">Scheduled jobs</p>
              <div className="space-y-1.5">
                {role.jobs.map(j => <JobRowView key={j.name} service={service} j={j} busy={busy} act={act} />)}
              </div>
            </div>
          )}
          {!!role.loops?.length && (
            <div>
              <p className="text-xs uppercase tracking-wide text-gray-500 mb-1.5">Loops</p>
              <div className="flex flex-wrap gap-1.5">
                {role.loops.map(l => <Badge key={l} variant={role.running ? 'success' : 'neutral'}><Activity className="w-3 h-3" />{l}</Badge>)}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function ConsumerRowView({ service, c, busy, act }: { service: ServiceInfo; c: ConsumerInfo; busy: string | null; act: Act }) {
  const k = (a: string) => `${service.name}:${c.name}:${a}`;
  const troubled = !!c.last_error || c.backoff_s > 0;
  return (
    <div className="rounded-md bg-gray-900 px-3 py-2">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm">
        <span className="font-mono text-gray-200">{c.name}</span>
        <span className="text-gray-500">{c.types.join(', ')}</span>
        <span className="text-gray-400 tabular">offset {fmtInt(c.offset)}</span>
        <span className="text-gray-400 tabular">{fmtInt(c.processed)} processed</span>
        {c.failed > 0 && <span className="text-red-400 tabular">{fmtInt(c.failed)} failed</span>}
        {c.waiting_gap != null && <Badge variant="warning" title="An earlier event id is not committed yet; waiting before passing it">waiting on id {c.waiting_gap}</Badge>}
        {c.backoff_s > 0 && <Badge variant="warning">retry in {fmtSeconds(c.backoff_s)}</Badge>}
        {!c.running && <Badge>paused</Badge>}
        {troubled && (
          <span className="ml-auto flex gap-1.5">
            <Button small icon={Zap} busy={busy === k('retry')} disabled={!!busy}
              onClick={() => act(k('retry'), () => dev.retryConsumer(service.name, c.name), `${c.name} retries now.`)}>
              Retry now
            </Button>
            <Button small icon={SkipForward} variant="danger" busy={busy === k('skip')} disabled={!!busy}
              onClick={() => {
                const v = window.prompt(
                  `Skip events for ${c.name} up to and including which event id? Skipped events are not processed by this consumer. ` +
                  'Find the failing id under Events.', String(c.offset + 1));
                const id = v ? Number(v) : NaN;
                if (!Number.isFinite(id) || id <= c.offset) return;
                void act(k('skip'), () => dev.skipEvent(service.name, c.name, id), `${c.name} will move past event ${id}.`);
              }}>
              Skip…
            </Button>
          </span>
        )}
      </div>
      {c.last_error && (
        <p className="mt-1 text-sm text-red-300 break-words" title={c.last_error_at ? fmtWhen(c.last_error_at) : undefined}>
          {c.last_error}
        </p>
      )}
    </div>
  );
}

function JobRowView({ service, j, busy, act }: { service: ServiceInfo; j: JobInfo; busy: string | null; act: Act }) {
  const k = `${service.name}:job:${j.name}`;
  const statusVariant: BadgeVariant = j.last_status === 'ok' ? 'success' : j.last_status === 'failed' ? 'danger'
    : j.last_status === 'running' ? 'info' : 'neutral';
  return (
    <div className="rounded-md bg-gray-900 px-3 py-2">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm">
        <span className="font-mono text-gray-200">{j.name}</span>
        <span className="text-gray-500">{j.schedule}</span>
        {!j.enabled && <Badge>disabled</Badge>}
        {j.running ? <Badge variant="info">running</Badge> : j.last_status && <Badge variant={statusVariant}>{j.last_status}</Badge>}
        <span className="text-gray-400" title={fmtWhen(j.next_run)}>next {j.next_run ? fmtAgo(j.next_run) : '—'}</span>
        {j.last_started && <span className="text-gray-500" title={fmtWhen(j.last_started)}>last {fmtAgo(j.last_started)} · {fmtSeconds(j.last_seconds)}</span>}
        <span className="text-gray-500 tabular">{fmtInt(j.runs)} runs</span>
        <span className="ml-auto">
          <Button small icon={Play} busy={busy === k} disabled={!!busy || j.running}
            onClick={() => act(k, () => dev.runJob(service.name, j.name), `${j.name} started.`)}>
            Run now
          </Button>
        </span>
      </div>
      <p className="text-sm text-gray-500 mt-0.5">{j.description}</p>
      {j.last_error && <p className="mt-1 text-sm text-red-300 break-words">{j.last_error}</p>}
    </div>
  );
}
