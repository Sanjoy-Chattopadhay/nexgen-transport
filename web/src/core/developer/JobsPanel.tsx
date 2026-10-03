import { useCallback, useEffect, useState } from 'react';
import { RefreshCw } from 'lucide-react';
import { dev, type JobRun, type Overview } from './api';
import { Badge, Button, Card, Empty, ErrorNote, Spinner, type BadgeVariant } from '../ui';
import { fmtSeconds, fmtWhen } from './format';

const STATUS: Record<string, BadgeVariant> = { ok: 'success', failed: 'danger', running: 'info', skipped: 'neutral' };

/** Every scheduled, manual and event-triggered job run, newest first. */
export default function JobsPanel({ overview }: { overview: Overview }) {
  const [service, setService] = useState('');
  const [runs, setRuns] = useState<JobRun[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setRuns((await dev.jobs(service || undefined, 300)).runs);
      setError(null);
    } catch (e: any) {
      setError(e?.message || String(e));
    } finally {
      setLoading(false);
    }
  }, [service]);

  useEffect(() => { void load(); }, [load]);

  return (
    <Card title="Job runs" subtitle="Every run of every job: on its schedule, from Run now, at boot or when an event asked for it."
      actions={(
        <>
          <select value={service} onChange={e => setService(e.target.value)}
            className="bg-field border border-gray-700 rounded-lg px-2.5 py-1.5 text-sm text-gray-200">
            <option value="">All services</option>
            {overview.services.map(s => <option key={s.name} value={s.name}>{s.title}</option>)}
          </select>
          <Button icon={RefreshCw} busy={loading} onClick={load}>Refresh</Button>
        </>
      )}>
      {error && <ErrorNote>{error}</ErrorNote>}
      {!runs && !error ? <Spinner /> : runs && runs.length === 0 ? (
        <Empty>No job has run yet. Scheduled jobs show their next run time on the Services tab.</Empty>
      ) : runs && (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs uppercase tracking-wide text-gray-500 border-b border-gray-800">
                <th className="py-2 pr-3 font-medium">Started</th>
                <th className="py-2 pr-3 font-medium">Service</th>
                <th className="py-2 pr-3 font-medium">Job</th>
                <th className="py-2 pr-3 font-medium">Trigger</th>
                <th className="py-2 pr-3 font-medium">Status</th>
                <th className="py-2 pr-3 font-medium text-right">Took</th>
                <th className="py-2 font-medium">Result</th>
              </tr>
            </thead>
            <tbody>
              {runs.map(r => (
                <tr key={r.i_run_id} className="border-b border-gray-800/60 align-top">
                  <td className="py-2 pr-3 text-gray-300 whitespace-nowrap tabular">{fmtWhen(r.dt_started)}</td>
                  <td className="py-2 pr-3 text-gray-300">{r.s_service}</td>
                  <td className="py-2 pr-3 font-mono text-gray-200">{r.s_job}</td>
                  <td className="py-2 pr-3 text-gray-400">{r.s_trigger}</td>
                  <td className="py-2 pr-3"><Badge variant={STATUS[r.s_status] ?? 'neutral'}>{r.s_status}</Badge></td>
                  <td className="py-2 pr-3 text-right text-gray-300 tabular whitespace-nowrap">{fmtSeconds(r.d_seconds)}</td>
                  <td className="py-2 text-gray-400 max-w-xl">
                    {r.s_error ? <span className="text-red-300 break-words">{r.s_error}</span>
                      : r.j_summary ? <code className="text-xs break-all">{summary(r.j_summary)}</code> : '—'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}

function summary(j: string): string {
  const s = typeof j === 'string' ? j : JSON.stringify(j);
  return s.length > 300 ? `${s.slice(0, 300)}…` : s;
}
