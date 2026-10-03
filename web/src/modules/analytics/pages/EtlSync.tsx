import { useState } from 'react';
import {
  Play, History, ShieldCheck, FileText, AlertCircle, Database,
} from 'lucide-react';
import PageContainer from '../components/layout/PageContainer';
import ProgressBar from '../components/ui/ProgressBar';
import LaneCard from '../components/etl/LaneCard';
import SchedulerHealth from '../components/etl/SchedulerHealth';
import DataGapBanner, { type DataGap } from '../components/etl/DataGapBanner';
import { usePolling } from '../hooks/usePolling';
import { formatNumber } from '../lib/formatters';
import {
  getSyncStatus, getSyncRuns, getSyncLogs, getSchedulerState, startBackfill,
  getWindowPreview, snoozeGap, dismissGap,
  LANE_KEYS, type LaneKey, type SyncStatus, type SyncRun, type SchedulerState,
  type EtlLog, type EtlLogEvent, type WindowPlan,
} from '../services/etlSync';

const KIND_STYLE: Record<string, string> = {
  run: 'bg-blue-900/40 text-blue-300',
  auth: 'bg-purple-900/40 text-purple-300',
  trips: 'bg-emerald-900/40 text-emerald-300',
  gps: 'bg-cyan-900/40 text-cyan-300',
};

function runSummary(e: EtlLogEvent): string {
  if (e.status === 'started')
    return `window ${(e.window_start || '').slice(11, 16)}–${(e.window_end || '').slice(11, 16)}`;
  return `fetched ${e.trips_fetched ?? 0} · passed ${e.trips_passed ?? 0} · failed ${e.trips_failed ?? 0}`
    + ` · GPS for ${e.trips_with_gps ?? 0}/${e.trips_fetched ?? 0} trips (${e.gps_records ?? 0} pts`
    + `${e.gps_deferred ? `, ${e.gps_deferred} deferred` : ''})`;
}

function LogRow({ e }: { e: EtlLogEvent }) {
  const [open, setOpen] = useState(false);
  const isErr = !!e.error || (e.status != null && e.status !== 200 && e.status !== 'ok' && e.status !== 'started' && e.status !== 'partial');
  const hasDetail = e.payload != null || e.params != null || e.error != null;
  const isRun = e.kind === 'run';
  return (
    <>
      <tr className={`border-b border-gray-800/50 ${hasDetail ? 'cursor-pointer hover:bg-gray-800/30' : ''}`}
        onClick={() => hasDetail && setOpen(o => !o)}>
        <td className="py-1.5 pr-3 text-gray-400 whitespace-nowrap font-mono text-xs">{(e.ts || '').slice(11, 19)}</td>
        <td className="py-1.5 pr-3"><span className={`px-1.5 py-0.5 rounded text-xs font-medium ${KIND_STYLE[e.kind] || 'bg-gray-800 text-gray-300'}`}>{e.kind}{e.detail ? `:${e.detail}` : ''}</span></td>
        <td className="py-1.5 pr-3 text-gray-400 font-mono text-xs">{e.method || ''}</td>
        <td className={`py-1.5 pr-3 text-xs ${isRun ? 'text-gray-300' : 'text-gray-400 truncate max-w-[280px]'}`}>{isRun ? runSummary(e) : (e.url || '')}</td>
        <td className="py-1.5 pr-3 text-right">
          <span className={`text-xs font-medium ${isErr ? 'text-red-400' : 'text-emerald-400'}`}>{e.status ?? '—'}</span>
        </td>
        <td className="py-1.5 pr-3 text-right text-gray-500 text-xs whitespace-nowrap">{e.elapsed_ms != null ? `${e.elapsed_ms}ms` : ''}</td>
      </tr>
      {open && hasDetail && (
        <tr className="bg-gray-950/60">
          <td colSpan={6} className="px-3 py-2">
            {e.error && <div className="text-red-400 text-xs mb-1"><AlertCircle className="w-3 h-3 inline mr-1" />{e.error}</div>}
            {e.payload != null && <pre className="text-xs text-gray-400 overflow-x-auto">payload: {JSON.stringify(e.payload)}</pre>}
            {e.params != null && <pre className="text-xs text-gray-400 overflow-x-auto">params: {JSON.stringify(e.params)}</pre>}
          </td>
        </tr>
      )}
    </>
  );
}

function fmtDt(s?: string | null) {
  if (!s) return '—';
  const d = new Date(s);
  return isNaN(d.getTime()) ? s : d.toLocaleString();
}

export default function EtlSync() {
  // Nudged after a manual run / settings save so the next poll is immediate.
  const [refreshTick, setRefreshTick] = useState(0);
  const bump = () => setRefreshTick(t => t + 1);

  const status = usePolling<SyncStatus>(() => getSyncStatus(), 4000, true);
  const sched = usePolling<SchedulerState>(() => getSchedulerState(), 5000, true);
  const runsResp = usePolling<{ runs: SyncRun[] }>(() => getSyncRuns(40), 5000, true);
  const runs = runsResp?.runs || [];

  const [logKind, setLogKind] = useState('');
  const [logErrorsOnly, setLogErrorsOnly] = useState(false);
  const logResp = usePolling<EtlLog>(() => getSyncLogs(300, logKind, logErrorsOnly), 4000, true);
  const logEvents = logResp?.events || [];

  // What the next run of each lane will actually cover. Slower cadence than
  // the status poll — the answer moves with wall-clock, not with events.
  const preview = usePolling<{ max_window_hours: number; lanes: Record<LaneKey, WindowPlan> }>(
    () => getWindowPreview(), 15000, true);

  // Backfill (one at a time, so the lane is chosen explicitly)
  const [bf, setBf] = useState<{ lane: LaneKey; from_date: string; to_date: string; chunk_days: number }>({
    lane: 'zonal', from_date: '', to_date: '', chunk_days: 1,
  });
  const [bfMsg, setBfMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const backfill = status?.backfill;
  const bfRunning = backfill?.status === 'running';

  // Hand a reported gap straight to the backfill form, already filled in, so
  // recovering is one click instead of transcribing dates out of a log line.
  const recoverGap = (lane: LaneKey, gap: DataGap) => {
    setBf({ lane, from_date: gap.from.slice(0, 10), to_date: gap.to.slice(0, 10), chunk_days: 1 });
    document.getElementById('etl-backfill')?.scrollIntoView({ behavior: 'smooth', block: 'center' });
  };

  // Snooze / dismiss. The row is hidden optimistically for 10s so the button
  // cannot be double-fired before the 4s status poll catches up — and then the
  // filter lifts, so a gap that genuinely RE-OPENS (it widened) comes back
  // instead of being hidden forever by a stale client-side decision.
  const [gapBusy, setGapBusy] = useState<number | null>(null);
  const [decided, setDecided] = useState<Set<number>>(new Set());
  const forget = (id: number) => setDecided(prev => {
    const next = new Set(prev); next.delete(id); return next;
  });
  const decideGap = async (id: number, action: 'snooze' | 'dismiss') => {
    setGapBusy(id);
    try {
      await (action === 'snooze' ? snoozeGap(id, 24) : dismissGap(id));
      setDecided(prev => new Set(prev).add(id));
      setTimeout(() => forget(id), 10000);
      bump();
    } catch (e: any) {
      setBfMsg({ ok: false, text: e?.response?.data?.detail || e.message || 'Could not update that gap' });
    } finally {
      setGapBusy(null);
    }
  };
  const visibleGaps = (status?.data_gaps || []).filter(g => !decided.has(g.id));

  const triggerBackfill = async () => {
    setBfMsg(null);
    if (!bf.from_date || !bf.to_date) { setBfMsg({ ok: false, text: 'Pick a from and to date.' }); return; }
    try {
      const res = await startBackfill(bf.lane, bf.from_date, bf.to_date, Number(bf.chunk_days) || 1);
      setBfMsg({ ok: res.data.status === 'started', text: res.data.message || res.data.status });
      bump();
    } catch (e: any) {
      setBfMsg({ ok: false, text: e?.response?.data?.detail || e.message || 'Backfill failed' });
    }
  };

  return (
    <PageContainer title="ETL — Scheduled API Sync">
      <p className="-mt-2 mb-5 text-sm text-gray-500 max-w-3xl">
        Two independent lanes pull from eTrans on their own cadence and write the same tables,
        tagged apart by trip class. Both resolve GPS through the same waypoint endpoint.
      </p>

      {/* ---- Missing data first: nothing else matters if a range was skipped ---- */}
      <DataGapBanner
        gaps={visibleGaps}
        onRecover={recoverGap}
        onSnooze={id => decideGap(id, 'snooze')}
        onDismiss={id => decideGap(id, 'dismiss')}
        busyId={gapBusy}
      />

      {/* ---- Scheduler health: is the worker actually alive? ---- */}
      <SchedulerHealth state={sched} />

      {/* ---- One module per lane ---- */}
      <div className="grid grid-cols-1 xl:grid-cols-2 gap-5 mb-5">
        {LANE_KEYS.map(lane => (
          <LaneCard
            key={`${lane}-${refreshTick}`}
            lane={lane}
            status={status?.lanes?.[lane]}
            plan={preview?.lanes?.[lane]}
            runs={runs.filter(r => r.lane === lane).slice(0, 5)}
            onChanged={bump}
          />
        ))}
      </div>

      {/* ---- Shared auth (one token serves both lanes) ---- */}
      <div className="bg-gray-900 rounded-xl border border-gray-800 p-5 mb-5">
        <div className="flex items-center gap-2 mb-3">
          <ShieldCheck className="w-5 h-5 text-blue-400" />
          <h2 className="text-lg font-semibold text-white">Upstream auth</h2>
          <span className="text-xs text-gray-500">shared by both lanes</span>
        </div>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          <div>
            <span className="text-gray-500 block text-xs">Token</span>
            <span className={`text-sm font-medium ${status?.auth?.authenticated ? 'text-emerald-400' : 'text-gray-400'}`}>
              {status?.auth?.authenticated ? 'valid' : 'not authenticated'}
            </span>
          </div>
          <div>
            <span className="text-gray-500 block text-xs">Expires</span>
            <span className="text-sm text-gray-200">{fmtDt(status?.auth?.access_expires_at)}</span>
          </div>
          <div>
            <span className="text-gray-500 block text-xs">Refresh token</span>
            <span className="text-sm text-gray-200">{status?.auth?.has_refresh_token ? 'held' : '—'}</span>
          </div>
          <div>
            <span className="text-gray-500 block text-xs">Any lane running</span>
            <span className={`text-sm font-medium ${status?.any_running ? 'text-blue-400' : 'text-gray-400'}`}>
              {status?.any_running ? 'yes' : 'idle'}
            </span>
          </div>
        </div>
      </div>

      {/* ---- Combined run history ---- */}
      <div className="bg-gray-900 rounded-xl border border-gray-800 p-5 mb-5">
        <div className="flex items-center gap-2 mb-4">
          <History className="w-5 h-5 text-blue-400" />
          <h2 className="text-lg font-semibold text-white">All Runs</h2>
          <span className="text-xs text-gray-500">both lanes, newest first · records processed into each table</span>
        </div>
        {runs.length === 0 ? (
          <p className="text-gray-500 text-sm">No runs yet — trigger one with a lane's “Run now”.</p>
        ) : (
          <div className="overflow-x-auto max-h-[420px] overflow-y-auto">
            <table className="w-full text-sm">
              <thead className="sticky top-0 bg-gray-900">
                <tr className="text-left text-xs text-gray-500 uppercase tracking-wider border-b border-gray-800">
                  <th className="py-2 pr-4 font-medium">Started</th>
                  <th className="py-2 pr-4 font-medium">Lane</th>
                  <th className="py-2 pr-4 font-medium">Trigger</th>
                  <th className="py-2 pr-4 font-medium">Status</th>
                  <th className="py-2 pr-4 font-medium text-right">tta_trips</th>
                  <th className="py-2 pr-4 font-medium text-right">tta_trip_gps</th>
                  <th className="py-2 pr-4 font-medium text-right">dup-skipped</th>
                  <th className="py-2 pr-4 font-medium text-right">GPS deferred</th>
                  <th className="py-2 pr-4 font-medium text-right">took</th>
                </tr>
              </thead>
              <tbody>
                {runs.map(r => (
                  <tr key={r.id} className="border-b border-gray-800/50 hover:bg-gray-800/30">
                    <td className="py-2 pr-4 text-gray-300 whitespace-nowrap">{fmtDt(r.started_at || r.created_at)}</td>
                    <td className="py-2 pr-4">
                      <span className={`px-1.5 py-0.5 rounded text-xs font-medium ${
                        r.lane === 'local' ? 'bg-emerald-900/40 text-emerald-400' : 'bg-blue-900/40 text-blue-400'}`}>
                        {r.lane || 'zonal'}
                      </span>
                    </td>
                    <td className="py-2 pr-4 text-gray-400">{r.trigger_type}</td>
                    <td className="py-2 pr-4">
                      <span className={`px-2 py-0.5 rounded-full text-xs ${
                        r.status === 'ok' ? 'bg-emerald-900/40 text-emerald-400'
                          : r.status === 'partial' ? 'bg-amber-900/40 text-amber-400'
                            : 'bg-red-900/40 text-red-400'}`}
                        title={r.status === 'partial' ? 'Some GPS deferred — window will be retried automatically' : undefined}>
                        {r.status}
                      </span>
                    </td>
                    <td className="py-2 pr-4 text-right text-emerald-400 font-medium">{formatNumber(r.trips_upserted)}</td>
                    <td className="py-2 pr-4 text-right text-emerald-400 font-medium">{formatNumber(r.gps_inserted)}</td>
                    <td className="py-2 pr-4 text-right text-amber-400">{formatNumber(r.gps_skipped)}</td>
                    <td className={`py-2 pr-4 text-right ${r.gps_failed > 0 ? 'text-amber-400 font-medium' : 'text-gray-500'}`}>{formatNumber(r.gps_failed || 0)}</td>
                    <td className="py-2 pr-4 text-right text-gray-400 whitespace-nowrap">{r.elapsed_seconds != null ? `${Math.round(r.elapsed_seconds)}s` : '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* ---- Backfill ---- */}
      <div id="etl-backfill" className="bg-gray-900 rounded-xl border border-gray-800 p-5">
        <div className="flex items-center gap-2 mb-1">
          <Database className="w-5 h-5 text-blue-400" />
          <h2 className="text-lg font-semibold text-white">Bounded Backfill</h2>
        </div>
        <p className="text-xs text-gray-500 mb-4">
          Pull a historical date range for one lane, in day-sized chunks. Safe to re-run —
          duplicates are skipped. Does not affect that lane's live watermark.
        </p>
        <div className="grid grid-cols-1 md:grid-cols-5 gap-4 items-end">
          <div>
            <label className="text-xs text-gray-500 block mb-1">Lane</label>
            <select value={bf.lane} onChange={e => setBf({ ...bf, lane: e.target.value as LaneKey })}
              className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-1.5 text-sm text-gray-200">
              {LANE_KEYS.map(l => (
                <option key={l} value={l}>{status?.lanes?.[l]?.label || l}</option>
              ))}
            </select>
          </div>
          <div>
            <label className="text-xs text-gray-500 block mb-1">From date</label>
            <input type="date" value={bf.from_date} onChange={e => setBf({ ...bf, from_date: e.target.value })}
              className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-1.5 text-sm text-gray-200" />
          </div>
          <div>
            <label className="text-xs text-gray-500 block mb-1">To date</label>
            <input type="date" value={bf.to_date} onChange={e => setBf({ ...bf, to_date: e.target.value })}
              className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-1.5 text-sm text-gray-200" />
          </div>
          <div>
            <label className="text-xs text-gray-500 block mb-1">Chunk (days)</label>
            <input type="number" min={1} value={bf.chunk_days} onChange={e => setBf({ ...bf, chunk_days: Number(e.target.value) })}
              className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-1.5 text-sm text-gray-200" />
          </div>
          <button onClick={triggerBackfill} disabled={bfRunning}
            className="flex items-center justify-center gap-1.5 px-3 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-lg text-xs font-medium disabled:opacity-50">
            <Play className="w-3.5 h-3.5" /> Start backfill
          </button>
        </div>
        {bfMsg && <p className={`mt-3 text-xs ${bfMsg.ok ? 'text-emerald-400' : 'text-red-400'}`}>{bfMsg.text}</p>}
        {backfill && backfill.status !== 'never' && (
          <div className="mt-4 pt-4 border-t border-gray-800">
            <ProgressBar percent={backfill.percent || 0}
              label={`Backfill ${backfill.lane ? `(${backfill.lane}) ` : ''}${backfill.chunks_done || 0}/${backfill.total_chunks || 0} chunks`}
              color="bg-blue-500" />
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mt-3">
              <div><span className="text-gray-500 block text-xs">Status</span><span className={`text-sm ${backfill.status === 'error' ? 'text-red-400' : 'text-gray-200'}`}>{backfill.status}</span></div>
              <div><span className="text-gray-500 block text-xs">Trips upserted</span><span className="text-sm text-emerald-400">{formatNumber(backfill.trips_upserted || 0)}</span></div>
              <div><span className="text-gray-500 block text-xs">GPS inserted</span><span className="text-sm text-emerald-400">{formatNumber(backfill.gps_inserted || 0)}</span></div>
              <div><span className="text-gray-500 block text-xs">GPS dup-skipped</span><span className="text-sm text-amber-400">{formatNumber(backfill.gps_skipped || 0)}</span></div>
            </div>
            {backfill.errors && backfill.errors.length > 0 && (
              <p className="text-red-400 text-xs mt-2">{backfill.errors.length} chunk error(s): {backfill.errors[0]}</p>
            )}
          </div>
        )}
      </div>

      {/* ---- ETL event log (today only) ---- */}
      <div className="bg-gray-900 rounded-xl border border-gray-800 p-5 mt-5">
        <div className="flex items-center justify-between mb-1 flex-wrap gap-2">
          <div className="flex items-center gap-2">
            <FileText className="w-5 h-5 text-blue-400" />
            <h2 className="text-lg font-semibold text-white">ETL Log</h2>
            <span className="text-xs text-gray-500">today only · {logResp?.total ?? 0} events</span>
          </div>
          <div className="flex items-center gap-2 text-xs">
            {['', 'run', 'auth', 'trips', 'gps'].map(k => (
              <button key={k || 'all'} onClick={() => setLogKind(k)}
                className={`px-2 py-1 rounded ${logKind === k ? 'bg-blue-600 text-white' : 'bg-gray-800 text-gray-400 hover:text-gray-200'}`}>
                {k || 'all'}
              </button>
            ))}
            <label className="flex items-center gap-1 text-gray-400 ml-1 cursor-pointer">
              <input type="checkbox" checked={logErrorsOnly} onChange={e => setLogErrorsOnly(e.target.checked)} className="accent-red-600" />
              errors only
            </label>
          </div>
        </div>
        <p className="text-xs text-gray-600 mb-3">
          Each external call's payload (secrets masked), time, response status and exception.
          Run events are tagged <code>run:&lt;lane&gt;:&lt;trigger&gt;</code>. Click a row for payload detail.
          {logResp?.file && <span className="font-mono"> {logResp.file}</span>}
        </p>
        {logEvents.length === 0 ? (
          <p className="text-gray-500 text-sm">No events today yet.</p>
        ) : (
          <div className="overflow-x-auto max-h-[420px] overflow-y-auto">
            <table className="w-full text-sm">
              <thead className="sticky top-0 bg-gray-900">
                <tr className="text-left text-xs text-gray-500 uppercase tracking-wider border-b border-gray-800">
                  <th className="py-2 pr-3 font-medium">Time</th>
                  <th className="py-2 pr-3 font-medium">Kind</th>
                  <th className="py-2 pr-3 font-medium">Method</th>
                  <th className="py-2 pr-3 font-medium">URL / summary</th>
                  <th className="py-2 pr-3 font-medium text-right">Status</th>
                  <th className="py-2 pr-3 font-medium text-right">Elapsed</th>
                </tr>
              </thead>
              <tbody>
                {logEvents.map((e, i) => <LogRow key={`${e.ts}-${i}`} e={e} />)}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </PageContainer>
  );
}
