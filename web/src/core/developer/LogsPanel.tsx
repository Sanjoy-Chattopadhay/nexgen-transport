import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Pause, Play, RefreshCw } from 'lucide-react';
import { dev, type Overview } from './api';
import { Button, Card, ErrorNote } from '../ui';

const LEVEL_CLASS: [RegExp, string][] = [
  [/\b(ERROR|CRITICAL|Traceback)\b/, 'text-red-300'],
  [/\bWARNING\b/, 'text-amber-300'],
];

/**
 * A service's log, or the supervisor's. `console` shows what the process
 * printed (start-up crashes land there before logging is configured).
 */
export default function LogsPanel({ overview, service, setService }: {
  overview: Overview; service: string; setService: (s: string) => void;
}) {
  const [lines, setLines] = useState(300);
  const [consoleOut, setConsoleOut] = useState(false);
  const [follow, setFollow] = useState(true);
  const [filter, setFilter] = useState('');
  const [text, setText] = useState<string[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const box = useRef<HTMLPreElement>(null);
  const stick = useRef(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setText((await dev.logs(service, lines, consoleOut)).lines);
      setError(null);
    } catch (e: any) {
      setError(e?.message || String(e));
    } finally {
      setLoading(false);
    }
  }, [service, lines, consoleOut]);

  useEffect(() => { void load(); }, [load]);
  useEffect(() => {
    if (!follow) return;
    const id = window.setInterval(() => { void load(); }, 3000);
    return () => window.clearInterval(id);
  }, [follow, load]);

  // Keep the view at the bottom while following, unless the reader scrolled up.
  useEffect(() => {
    const el = box.current;
    if (el && stick.current) el.scrollTop = el.scrollHeight;
  }, [text]);

  const shown = useMemo(() => {
    if (!text) return [];
    const f = filter.trim().toLowerCase();
    return f ? text.filter(l => l.toLowerCase().includes(f)) : text;
  }, [text, filter]);

  const isService = service !== 'supervisor';

  return (
    <Card title="Logs" actions={(
      <>
        <select value={service} onChange={e => setService(e.target.value)}
          className="bg-field border border-gray-700 rounded-lg px-2.5 py-1.5 text-sm text-gray-200">
          <option value="supervisor">Supervisor & gateway</option>
          {overview.services.map(s => <option key={s.name} value={s.name}>{s.title}</option>)}
        </select>
        <select value={lines} onChange={e => setLines(Number(e.target.value))}
          className="bg-field border border-gray-700 rounded-lg px-2.5 py-1.5 text-sm text-gray-200">
          {[100, 300, 1000, 3000].map(n => <option key={n} value={n}>last {n} lines</option>)}
        </select>
        {isService && (
          <label className="flex items-center gap-1.5 text-sm text-gray-400">
            <input type="checkbox" checked={consoleOut} onChange={e => setConsoleOut(e.target.checked)} />
            console output
          </label>
        )}
        <Button icon={follow ? Pause : Play} onClick={() => setFollow(f => !f)}>{follow ? 'Pause' : 'Follow'}</Button>
        <Button icon={RefreshCw} busy={loading} onClick={load}>Refresh</Button>
      </>
    )}>
      <input value={filter} onChange={e => setFilter(e.target.value)} placeholder="Show only lines containing…"
        className="w-full mb-3 bg-field border border-gray-700 rounded-lg px-3 py-1.5 text-sm text-gray-200 outline-none focus:border-blue-600" />
      {error && <ErrorNote>{error}</ErrorNote>}
      <pre ref={box}
        onScroll={e => {
          const el = e.currentTarget;
          stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
        }}
        className="h-[60vh] overflow-auto rounded-lg bg-gray-950 border border-gray-800 p-3 font-mono text-xs leading-relaxed text-gray-300 whitespace-pre-wrap break-all">
        {text === null ? 'Loading…' : shown.length === 0 ? (filter ? 'No line matches.' : 'The log is empty.') : shown.map((l, i) => {
          const cls = LEVEL_CLASS.find(([re]) => re.test(l))?.[1];
          return <div key={i} className={cls}>{l}</div>;
        })}
      </pre>
    </Card>
  );
}
