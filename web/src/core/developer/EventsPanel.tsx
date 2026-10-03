import { useCallback, useEffect, useState } from 'react';
import { RefreshCw } from 'lucide-react';
import { dev, type ConsumerRow, type EventRow } from './api';
import { Badge, Button, Card, Empty, ErrorNote, Spinner } from '../ui';
import { fmtInt, fmtWhen } from './format';

const TYPES = ['', 'ingest.batch.landed', 'fleet.trips.changed', 'fleet.fixes.stored', 'geo.visits.changed',
  'route.analysed', 'ml.predictions.ready', 'platform.config.changed'];

/**
 * The event bus: each consumer's position against the newest event, and the
 * events themselves. A consumer that is behind (lag > 0) for long, or has a
 * last error, is the first place to look when a page is not updating.
 */
export default function EventsPanel() {
  const [type, setType] = useState('');
  const [data, setData] = useState<{ consumers: ConsumerRow[]; recent: EventRow[] } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [openId, setOpenId] = useState<number | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setData(await dev.events(100, type || undefined));
      setError(null);
    } catch (e: any) {
      setError(e?.message || String(e));
    } finally {
      setLoading(false);
    }
  }, [type]);

  useEffect(() => { void load(); }, [load]);

  return (
    <div className="space-y-4">
      {error && <ErrorNote>{error}</ErrorNote>}
      <Card title="Consumers" subtitle="Where each service has read the event log up to. Lag is how many events it has not handled yet."
        actions={<Button icon={RefreshCw} busy={loading} onClick={load}>Refresh</Button>}>
        {!data ? <Spinner /> : data.consumers.length === 0 ? <Empty>No consumer has started yet.</Empty> : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-xs uppercase tracking-wide text-gray-500 border-b border-gray-800">
                  <th className="py-2 pr-3 font-medium">Consumer</th>
                  <th className="py-2 pr-3 font-medium text-right">Read up to</th>
                  <th className="py-2 pr-3 font-medium text-right">Newest</th>
                  <th className="py-2 pr-3 font-medium text-right">Lag</th>
                  <th className="py-2 pr-3 font-medium text-right">Processed</th>
                  <th className="py-2 pr-3 font-medium text-right">Failed</th>
                  <th className="py-2 pr-3 font-medium">Updated</th>
                  <th className="py-2 font-medium">Last error</th>
                </tr>
              </thead>
              <tbody>
                {data.consumers.map(c => (
                  <tr key={c.s_consumer} className="border-b border-gray-800/60 align-top">
                    <td className="py-2 pr-3 font-mono text-gray-200">{c.s_consumer}</td>
                    <td className="py-2 pr-3 text-right tabular text-gray-300">{fmtInt(c.i_last_event_id)}</td>
                    <td className="py-2 pr-3 text-right tabular text-gray-400">{fmtInt(c.head)}</td>
                    <td className="py-2 pr-3 text-right">
                      <Badge variant={c.lag === 0 ? 'success' : c.lag < 50 ? 'info' : 'warning'}>{fmtInt(c.lag)}</Badge>
                    </td>
                    <td className="py-2 pr-3 text-right tabular text-gray-300">{fmtInt(c.i_processed)}</td>
                    <td className={`py-2 pr-3 text-right tabular ${c.i_failed ? 'text-red-400' : 'text-gray-500'}`}>{fmtInt(c.i_failed)}</td>
                    <td className="py-2 pr-3 text-gray-400 whitespace-nowrap">{fmtWhen(c.dt_updated)}</td>
                    <td className="py-2 text-red-300 break-words max-w-md">{c.s_last_error ?? <span className="text-gray-600">—</span>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <Card title="Recent events" subtitle="Each carries ids, windows and touched keys only, never the data itself."
        actions={(
          <select value={type} onChange={e => setType(e.target.value)}
            className="bg-field border border-gray-700 rounded-lg px-2.5 py-1.5 text-sm text-gray-200">
            {TYPES.map(t => <option key={t} value={t}>{t || 'Every type'}</option>)}
          </select>
        )}>
        {!data ? <Spinner /> : data.recent.length === 0 ? <Empty>No events yet.</Empty> : (
          <div className="space-y-1">
            {data.recent.map(e => (
              <div key={e.i_event_id} className="rounded-md bg-gray-950/40 border border-gray-800">
                <button onClick={() => setOpenId(openId === e.i_event_id ? null : e.i_event_id)}
                  className="w-full flex flex-wrap items-center gap-x-3 gap-y-1 px-3 py-2 text-sm text-left">
                  <span className="tabular text-gray-500">#{e.i_event_id}</span>
                  <span className="font-mono text-gray-200">{e.s_type}</span>
                  <span className="text-gray-500">from {e.s_source}</span>
                  {e.dt_created && <span className="text-gray-500 ml-auto">{fmtWhen(e.dt_created)}</span>}
                </button>
                {openId === e.i_event_id && (
                  <pre className="px-3 pb-3 text-xs text-gray-300 whitespace-pre-wrap break-all max-h-80 overflow-auto">
                    {pretty(e.j_payload)}
                  </pre>
                )}
              </div>
            ))}
          </div>
        )}
      </Card>
    </div>
  );
}

function pretty(j: unknown): string {
  try {
    return JSON.stringify(typeof j === 'string' ? JSON.parse(j) : j, null, 2);
  } catch {
    return String(j);
  }
}
