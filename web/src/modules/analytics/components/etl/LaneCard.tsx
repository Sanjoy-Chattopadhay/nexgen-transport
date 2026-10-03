import { useEffect, useState } from 'react';
import {
  Play, Save, RefreshCw, CheckCircle, XCircle, Globe2, MapPin, Timer, AlertTriangle,
} from 'lucide-react';
import { formatNumber } from '../../lib/formatters';
import {
  updateSyncSettings, runSyncNow,
  type LaneKey, type LaneStatus, type SyncSettings, type SyncRun, type WindowPlan,
} from '../../services/etlSync';

/** Per-lane accent, so zonal and local are told apart at a glance and match the
 *  sidebar's trip-class filter colours. */
const THEME: Record<LaneKey, {
  icon: typeof Globe2; ring: string; text: string; bg: string; btn: string; bar: string;
}> = {
  zonal: {
    icon: Globe2,
    ring: 'border-blue-900/60', text: 'text-blue-400', bg: 'bg-blue-600/10',
    btn: 'bg-blue-600 hover:bg-blue-700', bar: 'bg-blue-500',
  },
  local: {
    icon: MapPin,
    ring: 'border-emerald-900/60', text: 'text-emerald-400', bg: 'bg-emerald-600/10',
    btn: 'bg-emerald-600 hover:bg-emerald-700', bar: 'bg-emerald-500',
  },
};

function fmtDt(s?: string | null) {
  if (!s) return '—';
  const d = new Date(s);
  return isNaN(d.getTime()) ? s : d.toLocaleString();
}

/** Hours only read as a duration up to a couple of days. */
function span(hours: number): string {
  if (hours < 48) return `${Math.round(hours)}h`;
  const days = hours / 24;
  return days < 10 ? `${days.toFixed(1)}d` : `${Math.round(days)}d`;
}

/** Live "fires in mm:ss" so the scheduler can be watched actually counting
 *  down, rather than inferred from a config value. */
function useCountdown(iso: string | null | undefined) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);
  if (!iso) return null;
  const target = new Date(iso).getTime();
  if (isNaN(target)) return null;
  const secs = Math.round((target - now) / 1000);
  if (secs <= 0) return 'due now';
  const h = Math.floor(secs / 3600);
  const m = Math.floor((secs % 3600) / 60);
  const s = secs % 60;
  return h > 0 ? `${h}h ${m}m` : `${m}m ${String(s).padStart(2, '0')}s`;
}

function Stat({ label, value, tone = 'text-gray-200' }:
  { label: string; value: React.ReactNode; tone?: string }) {
  return (
    <div>
      <span className="text-gray-500 block text-xs">{label}</span>
      <span className={`${tone} text-sm font-medium`}>{value}</span>
    </div>
  );
}

interface Props {
  lane: LaneKey;
  status?: LaneStatus;
  /** What this lane's next run will cover. Absent on an older backend. */
  plan?: WindowPlan;
  runs: SyncRun[];
  onChanged: () => void;
}

export default function LaneCard({ lane, status, plan, runs, onChanged }: Props) {
  const theme = THEME[lane];
  const Icon = theme.icon;
  const countdown = useCountdown(status?.next_scheduled_run);

  const [form, setForm] = useState<Partial<SyncSettings> | null>(null);
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [starting, setStarting] = useState(false);

  // Seed the form once from the server, then leave it under the user's control.
  useEffect(() => {
    if (status && !form) {
      setForm({
        enabled: status.enabled,
        interval_minutes: status.interval_minutes,
        lookback_minutes: status.lookback_minutes,
        entity_ids: status.entity_ids,
      });
    }
  }, [status, form]);

  const save = async () => {
    if (!form) return;
    setSaving(true); setMsg(null);
    try {
      await updateSyncSettings(lane, {
        enabled: form.enabled,
        interval_minutes: Number(form.interval_minutes),
        lookback_minutes: Number(form.lookback_minutes),
        ...(status?.needs_entities ? { entity_ids: form.entity_ids } : {}),
      });
      setMsg({ ok: true, text: 'Saved — schedule re-applied.' });
      onChanged();
    } catch (e: any) {
      setMsg({ ok: false, text: e?.response?.data?.detail || e.message || 'Save failed' });
    } finally { setSaving(false); }
  };

  const runNow = async () => {
    setStarting(true); setMsg(null);
    try {
      const res = await runSyncNow(lane);
      setMsg({ ok: res.data.status === 'started', text: res.data.message || res.data.status });
      onChanged();
    } catch (e: any) {
      setMsg({ ok: false, text: e?.response?.data?.detail || e.message || 'Trigger failed' });
    } finally { setTimeout(() => setStarting(false), 1200); }
  };

  const lr = status?.last_run;
  const notReady = status && !status.ready;

  return (
    <div className={`bg-gray-900 rounded-xl border ${theme.ring} overflow-hidden flex flex-col`}>
      {/* header */}
      <div className={`flex items-center justify-between px-5 py-3.5 ${theme.bg} border-b ${theme.ring}`}>
        <div className="flex items-center gap-2.5 min-w-0">
          <Icon className={`w-[18px] h-[18px] ${theme.text} shrink-0`} />
          <div className="min-w-0">
            <h3 className="text-sm font-semibold text-white leading-tight truncate">
              {status?.label || lane}
            </h3>
            <p className="text-xs text-gray-500 font-mono truncate">{status?.endpoint || ''}</p>
          </div>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          {status?.running && (
            <span className={`flex items-center gap-1 text-xs ${theme.text}`}>
              <RefreshCw className="w-3 h-3 animate-spin" /> running
            </span>
          )}
          {/* Only when the backend explicitly says unhealthy. An older backend
              omits the field entirely, and `undefined` must not read as "behind"
              — a health badge that lies is worse than no badge. */}
          {status?.enabled && status.healthy === false && (
            <span className="text-xs px-2 py-0.5 rounded-full font-medium bg-red-900/40 text-red-400"
              title={`Watermark is ${status.lag_minutes}m behind the source (limit ${status.lag_limit_minutes}m)`}>
              BEHIND
            </span>
          )}
          {/* A separate verdict from BEHIND on purpose. A lane can be perfectly
              current at the tail and still be missing a fortnight behind it —
              and a DISMISSED gap counts here too, so declining to backfill
              never restores a clean-looking card. */}
          {!!status?.gaps?.unresolved && (
            <span className="text-xs px-2 py-0.5 rounded-full font-medium bg-amber-900/40 text-amber-400"
              title={`${status.gaps.unresolved} recorded gap(s), ${span(status.gaps.missing_hours)} of data never fetched`
                + (status.gaps.dismissed ? ` — ${status.gaps.dismissed} dismissed` : '')}>
              {status.gaps.unresolved} GAP{status.gaps.unresolved > 1 ? 'S' : ''}
            </span>
          )}
          <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${
            status?.enabled ? 'bg-emerald-900/40 text-emerald-400' : 'bg-gray-800 text-gray-500'}`}>
            {status?.enabled ? 'ON' : 'OFF'}
          </span>
        </div>
      </div>

      {notReady && (
        <div className="mx-5 mt-4 flex items-start gap-2 rounded-lg border border-amber-800 bg-amber-900/20 px-3 py-2 text-xs text-amber-300">
          <AlertTriangle className="w-3.5 h-3.5 mt-px shrink-0" />
          <span>
            Not configured — set the TMS credentials
            {status?.needs_entities && <> and <code>TMS_ENTITY_IDS</code></>} in <code>.env</code>.
          </span>
        </div>
      )}

      {/* live status */}
      <div className="px-5 py-4 grid grid-cols-2 gap-x-4 gap-y-3">
        <Stat
          label="Next run"
          value={
            status?.enabled
              ? <span className="flex items-center gap-1.5">
                  <Timer className={`w-3.5 h-3.5 ${theme.text}`} />
                  <span className={`${theme.text} tabular-nums font-semibold`}>{countdown ?? '—'}</span>
                </span>
              : <span className="text-gray-500">not scheduled</span>
          }
        />
        <Stat label="Every" value={`${status?.interval_minutes ?? '—'} min`} />
        <Stat
          label="Lag (behind source)"
          value={
            status?.lag_minutes == null ? '—'
              : <span className="flex items-center gap-1.5">
                  <span className={status.healthy === false ? 'text-red-400' : 'text-emerald-400'}>
                    {status.lag_minutes < 60
                      ? `${Math.round(status.lag_minutes)}m`
                      : `${(status.lag_minutes / 60).toFixed(1)}h`}
                  </span>
                  {status.lag_limit_minutes != null && (
                    <span className="text-gray-600 text-xs">
                      / {Math.round(status.lag_limit_minutes)}m
                    </span>
                  )}
                </span>
          }
        />
        {/* The question the screen has to answer before any other: until when
            do I actually have data? The watermark alone overstates it whenever
            a gap sits behind the frontier, so the two are shown together. */}
        <Stat
          label="Data through"
          value={<span className="text-xs">{fmtDt(status?.data_through ?? status?.watermark)}</span>}
        />
        <Stat
          label="Known gaps"
          tone={status?.gaps?.unresolved ? 'text-amber-400' : 'text-gray-500'}
          value={
            status?.gaps == null ? '—'
              : status.gaps.unresolved === 0
                ? <span className="text-xs">none</span>
                : <span className="text-xs">
                    {status.gaps.unresolved} · {span(status.gaps.missing_hours)} missing
                  </span>
          }
        />
        <Stat label="Lookback" value={`${status?.lookback_minutes ?? '—'} min`} />
        {status?.needs_entities
          ? <Stat label="Entities" value={(status?.entity_ids || []).join(', ') || '—'} />
          : <Stat label="Scope" value={<span className="text-xs text-gray-400">from logged-in user</span>} />}
        <Stat label="Scheduled at" value={<span className="text-xs">{fmtDt(status?.next_scheduled_run)}</span>} />
      </div>

      {/* What the next run will actually pull. Stated outright because the
          answer is not obvious from the interval: the window is capped, so
          coming back after a fortnight fetches ONE capped window, not a
          fortnight — and the remainder is filed as a gap rather than fetched
          silently. */}
      {plan && (
        <div className="mx-5 -mt-1 mb-4 rounded-lg bg-gray-950/40 border border-gray-800/70 px-4 py-2.5">
          <div className="text-xs text-gray-500 uppercase tracking-wider mb-1">Next fetch covers</div>
          <div className="text-xs text-gray-300 font-mono break-words">
            {fmtDt(plan.window_start)} <span className="text-gray-600">→</span> {fmtDt(plan.window_end)}
          </div>
          {plan.unreachable_hours > 0 && (
            <div className="text-xs text-amber-400/90 mt-1.5 leading-relaxed">
              {span(plan.unreachable_hours)} older than the window cap will NOT be fetched —
              it is recorded as a gap for you to backfill or dismiss.
            </div>
          )}
        </div>
      )}

      {/* last run */}
      {lr && lr.status && lr.status !== 'never' && (
        <div className="mx-5 mb-4 rounded-lg bg-gray-950/60 border border-gray-800 px-4 py-3">
          <div className="text-xs text-gray-500 uppercase tracking-wider mb-2">
            Last run · {lr.trigger}
          </div>
          {lr.status === 'error' ? (
            <p className="text-red-400 text-xs break-words">{lr.error}</p>
          ) : (
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
              <Stat label="Trips" value={formatNumber(lr.trips_upserted || 0)} tone="text-emerald-400" />
              <Stat label="GPS" value={formatNumber(lr.gps_inserted || 0)} tone="text-emerald-400" />
              <Stat label="Dup-skipped" value={formatNumber(lr.gps_skipped || 0)} tone="text-amber-400" />
              <Stat label="Took" value={`${Math.round(lr.elapsed_seconds || 0)}s`} />
            </div>
          )}
        </div>
      )}

      {/* schedule editor */}
      {form && (
        <div className="px-5 pb-4 border-t border-gray-800 pt-4">
          <div className="grid grid-cols-2 gap-3">
            <label className="flex items-center gap-2 text-xs text-gray-300 col-span-2">
              <input type="checkbox" checked={!!form.enabled} disabled={!!notReady}
                onChange={e => setForm({ ...form, enabled: e.target.checked })}
                className="w-4 h-4 accent-blue-600 disabled:opacity-40" />
              Run this lane on a schedule
            </label>
            <div>
              <label className="text-xs text-gray-500 block mb-1">Interval (min)</label>
              <input type="number" min={1} value={form.interval_minutes ?? ''}
                onChange={e => setForm({ ...form, interval_minutes: Number(e.target.value) })}
                className="w-full bg-gray-800 border border-gray-700 rounded-lg px-2.5 py-1.5 text-sm text-gray-200" />
            </div>
            <div>
              <label className="text-xs text-gray-500 block mb-1">Lookback (min)</label>
              <input type="number" min={0} value={form.lookback_minutes ?? ''}
                onChange={e => setForm({ ...form, lookback_minutes: Number(e.target.value) })}
                className="w-full bg-gray-800 border border-gray-700 rounded-lg px-2.5 py-1.5 text-sm text-gray-200" />
            </div>
            {status?.needs_entities && (
              <div className="col-span-2">
                <label className="text-xs text-gray-500 block mb-1">Entity IDs (comma-separated)</label>
                <input type="text" value={(form.entity_ids || []).join(', ')}
                  onChange={e => setForm({
                    ...form,
                    entity_ids: e.target.value.split(',').map(x => Number(x.trim())).filter(n => !isNaN(n)),
                  })}
                  placeholder="e.g. 540"
                  className="w-full bg-gray-800 border border-gray-700 rounded-lg px-2.5 py-1.5 text-sm text-gray-200" />
              </div>
            )}
          </div>

          <div className="mt-3 flex items-center gap-2 flex-wrap">
            <button onClick={runNow} disabled={!!notReady || starting || status?.running}
              className={`flex items-center gap-1.5 px-3 py-1.5 ${theme.btn} text-white rounded-lg text-xs font-medium disabled:opacity-40`}>
              <Play className="w-3.5 h-3.5" /> Run now
            </button>
            <button onClick={save} disabled={saving}
              className="flex items-center gap-1.5 px-3 py-1.5 bg-gray-700 hover:bg-gray-600 text-white rounded-lg text-xs font-medium disabled:opacity-40">
              <Save className="w-3.5 h-3.5" /> Save
            </button>
            {msg && (
              <span className={`flex items-center gap-1 text-xs ${msg.ok ? 'text-emerald-400' : 'text-red-400'}`}>
                {msg.ok ? <CheckCircle className="w-3 h-3" /> : <XCircle className="w-3 h-3" />}
                {msg.text}
              </span>
            )}
          </div>
        </div>
      )}

      {/* per-lane run history */}
      <div className="px-5 pb-5 mt-auto">
        <div className="text-xs text-gray-500 uppercase tracking-wider mb-2">
          Recent runs · this lane
        </div>
        {runs.length === 0 ? (
          <p className="text-gray-600 text-xs">No runs yet.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-left text-xs text-gray-500 uppercase border-b border-gray-800">
                  <th className="py-1.5 pr-2 font-medium">Started</th>
                  <th className="py-1.5 pr-2 font-medium">Trigger</th>
                  <th className="py-1.5 pr-2 font-medium">Status</th>
                  <th className="py-1.5 pr-2 font-medium text-right">Trips</th>
                  <th className="py-1.5 pr-2 font-medium text-right">GPS</th>
                  <th className="py-1.5 font-medium text-right">Took</th>
                </tr>
              </thead>
              <tbody>
                {runs.map(r => (
                  <tr key={r.id} className="border-b border-gray-800/50">
                    <td className="py-1.5 pr-2 text-gray-300 whitespace-nowrap">
                      {fmtDt(r.started_at || r.created_at)}
                    </td>
                    <td className="py-1.5 pr-2 text-gray-500">{r.trigger_type}</td>
                    <td className="py-1.5 pr-2">
                      <span className={`px-1.5 py-0.5 rounded-full text-xs ${
                        r.status === 'ok' ? 'bg-emerald-900/40 text-emerald-400'
                          : r.status === 'partial' ? 'bg-amber-900/40 text-amber-400'
                            : 'bg-red-900/40 text-red-400'}`}
                        title={r.status === 'partial'
                          ? 'Some GPS deferred — window retried automatically' : undefined}>
                        {r.status}
                      </span>
                    </td>
                    <td className="py-1.5 pr-2 text-right text-emerald-400">{formatNumber(r.trips_upserted)}</td>
                    <td className="py-1.5 pr-2 text-right text-emerald-400">{formatNumber(r.gps_inserted)}</td>
                    <td className="py-1.5 text-right text-gray-500 whitespace-nowrap">
                      {r.elapsed_seconds != null ? `${Math.round(r.elapsed_seconds)}s` : '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
