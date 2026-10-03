import { AlertTriangle, Play, Clock, X } from 'lucide-react';
import type { DataGapRow, LaneKey } from '../../services/etlSync';

/**
 * A lane can only reach back TMS_MAX_WINDOW_HOURS in one run. If it was down
 * longer than that, the span before the cap is REAL MISSING DATA — recorded in
 * tta_data_gaps rather than silently clamped away.
 *
 * Backfilling is never forced: the scheduler resumes on its own and the app
 * works. What this banner guarantees is that declining is a DECISION, not
 * amnesia. Before the register, the warning lived on the in-memory `last_run`
 * and vanished with the next clean tick ~30 minutes later, leaving the lane
 * rendering green over a fortnight-wide hole.
 *
 * So there are three answers here and no way to make it disappear by ignoring
 * it: recover it now, remind me later, or dismiss it — and a dismissed gap
 * still counts on the lane card.
 */
export interface DataGap {
  from: string;
  to: string;
  hours: number;
}

interface Props {
  gaps?: DataGapRow[];
  /** Hands the range to the backfill form, already filled in. */
  onRecover: (lane: LaneKey, gap: DataGap) => void;
  onSnooze: (id: number) => void;
  onDismiss: (id: number) => void;
  busyId?: number | null;
}

const day = (iso: string) => (iso || '').slice(0, 10);
const stamp = (iso: string) => {
  const d = new Date(iso);
  return isNaN(d.getTime()) ? iso : d.toLocaleString();
};

/** Hours read badly past a day or two — "358h" is not a duration anyone feels. */
function span(hours: number): string {
  if (hours < 48) return `${Math.round(hours)} h`;
  const days = hours / 24;
  return days < 10 ? `${days.toFixed(1)} days` : `${Math.round(days)} days`;
}

export default function DataGapBanner({ gaps, onRecover, onSnooze, onDismiss, busyId }: Props) {
  const rows = gaps ?? [];
  if (rows.length === 0) return null;

  const totalHours = rows.reduce((sum, g) => sum + (g.hours || 0), 0);

  return (
    <div className="mb-5 rounded-xl border border-red-800 bg-red-950/40 p-4">
      <div className="flex items-start gap-3">
        <AlertTriangle className="w-5 h-5 text-red-400 shrink-0 mt-0.5" />
        <div className="flex-1 min-w-0">
          <h3 className="text-sm font-semibold text-red-300">
            Missing data — {rows.length === 1 ? 'one span was' : `${rows.length} spans were`} never
            fetched ({span(totalHours)} total)
          </h3>
          <p className="text-xs text-red-200/70 mt-1 mb-3 leading-relaxed">
            The sync was down longer than one window can reach back, so this range is
            <strong> not in the database</strong>. Live syncing has already resumed on its own —
            backfilling is optional. Re-running a range is safe: duplicates are ignored.
            <span className="text-red-300/60"> Dismissed spans stay counted on the lane card;
            they do not come back by themselves.</span>
          </p>
          <div className="space-y-2">
            {rows.map(g => {
              const busy = busyId === g.id;
              return (
                <div key={g.id}
                  className="flex items-center justify-between gap-3 flex-wrap rounded-lg bg-red-950/40 border border-red-900/60 px-3 py-2">
                  <div className="text-xs text-red-100 min-w-0">
                    <span className="font-semibold uppercase">{g.lane}</span>
                    <span className="text-red-300/70"> missing </span>
                    <span className="font-mono">{stamp(g.gap_start)}</span>
                    <span className="text-red-300/70"> → </span>
                    <span className="font-mono">{stamp(g.gap_end)}</span>
                    <span className="text-red-300/70"> ({span(g.hours)})</span>
                    {g.state === 'snoozed' && (
                      <span className="ml-2 text-xs px-1.5 py-0.5 rounded bg-amber-900/40 text-amber-300">
                        snooze expired
                      </span>
                    )}
                  </div>
                  <div className="flex items-center gap-1.5 shrink-0">
                    <button
                      onClick={() => onRecover(g.lane, { from: g.gap_start, to: g.gap_end, hours: g.hours })}
                      disabled={busy}
                      className="flex items-center gap-1.5 px-3 py-1.5 bg-red-600 hover:bg-red-700 disabled:opacity-50 text-white rounded-lg text-xs font-medium">
                      <Play className="w-3.5 h-3.5" />
                      Recover {day(g.gap_start)} → {day(g.gap_end)}
                    </button>
                    <button
                      onClick={() => onSnooze(g.id)}
                      disabled={busy}
                      title="Hide for 24 hours, then ask again"
                      className="flex items-center gap-1 px-2.5 py-1.5 bg-red-900/50 hover:bg-red-900 disabled:opacity-50 text-red-200 rounded-lg text-xs font-medium">
                      <Clock className="w-3.5 h-3.5" />
                      Later
                    </button>
                    <button
                      onClick={() => onDismiss(g.id)}
                      disabled={busy}
                      title="Decline this range — it stays counted as missing on the lane card"
                      className="flex items-center gap-1 px-2.5 py-1.5 bg-red-900/50 hover:bg-red-900 disabled:opacity-50 text-red-200 rounded-lg text-xs font-medium">
                      <X className="w-3.5 h-3.5" />
                      Dismiss
                    </button>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      </div>
    </div>
  );
}
