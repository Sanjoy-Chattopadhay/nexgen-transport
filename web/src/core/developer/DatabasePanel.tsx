import { useCallback, useEffect, useState } from 'react';
import { ChevronDown, ChevronRight, DatabaseZap, RefreshCw } from 'lucide-react';
import { dev, type MigrationStatus, type SchemaInfo } from './api';
import { Badge, Button, Card, ErrorNote, Spinner } from '../ui';
import { fmtBytes, fmtInt } from './format';

/**
 * Every schema NexGen owns (one per service) and the two legacy databases it
 * reads, with sizes, tables and migration status. Row counts are InnoDB's
 * estimates from information_schema, so they are approximate.
 */
export default function DatabasePanel() {
  const [data, setData] = useState<{ schemas: SchemaInfo[]; migrations: MigrationStatus[] } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [migrating, setMigrating] = useState(false);
  const [note, setNote] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setData(await dev.database());
      setError(null);
    } catch (e: any) {
      setError(e?.message || String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  const pending = data?.migrations.reduce((n, m) => n + (m.pending || 0), 0) ?? 0;

  const migrate = async () => {
    if (!window.confirm(`Apply ${pending} pending migration(s) now? Each schema's files are applied in order; views are refreshed.`)) return;
    setMigrating(true);
    setNote(null);
    try {
      const out = await dev.migrate();
      const applied = (out.applied || []).filter((r: any) => r.action === 'applied').length;
      setNote(`${applied} migration file(s) applied.`);
    } catch (e: any) {
      setNote(`Migration failed: ${e?.message || e}`);
    } finally {
      setMigrating(false);
      void load();
    }
  };

  if (error) return <ErrorNote>{error}</ErrorNote>;
  if (!data) return <Spinner />;

  const own = data.schemas.filter(s => !String(s.key).startsWith('legacy:'));
  const legacy = data.schemas.filter(s => String(s.key).startsWith('legacy:'));
  const total = own.reduce((n, s) => n + s.bytes, 0);

  return (
    <div className="space-y-4">
      <Card title="Migrations" icon={DatabaseZap}
        subtitle="Versioned files are applied once, in order, per schema; the R__ view files are re-applied on every run."
        actions={(
          <>
            <Button icon={RefreshCw} busy={loading} onClick={load}>Refresh</Button>
            <Button variant="primary" busy={migrating} disabled={pending === 0} onClick={migrate}>
              {pending ? `Apply ${pending} pending` : 'Nothing pending'}
            </Button>
          </>
        )}>
        {note && <p className="mb-3 text-sm text-gray-300">{note}</p>}
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs uppercase tracking-wide text-gray-500 border-b border-gray-800">
                <th className="py-2 pr-3 font-medium">Schema</th>
                <th className="py-2 pr-3 font-medium">Database</th>
                <th className="py-2 pr-3 font-medium text-right">Applied</th>
                <th className="py-2 pr-3 font-medium">Latest</th>
                <th className="py-2 pr-3 font-medium">Pending</th>
                <th className="py-2 font-medium">Notes</th>
              </tr>
            </thead>
            <tbody>
              {data.migrations.map((m, i) => (
                <tr key={m.schema ?? i} className="border-b border-gray-800/60 align-top">
                  <td className="py-2 pr-3 text-gray-200">{m.schema ?? '—'}</td>
                  <td className="py-2 pr-3 font-mono text-gray-400">{m.database}</td>
                  <td className="py-2 pr-3 text-right tabular text-gray-300">{m.applied}</td>
                  <td className="py-2 pr-3 font-mono text-gray-400">{m.latest ?? '—'}</td>
                  <td className="py-2 pr-3">
                    {m.pending ? <Badge variant="warning" title={m.pending_files.join(', ')}>{m.pending} pending</Badge>
                      : <Badge variant="success">up to date</Badge>}
                  </td>
                  <td className="py-2 text-sm">
                    {m.error && <span className="text-red-300">{m.error}</span>}
                    {!!m.changed_after_apply?.length && (
                      <span className="text-amber-300" title={m.changed_after_apply.join(', ')}>
                        {m.changed_after_apply.length} applied file(s) edited since — add a new numbered file instead
                      </span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>

      <Card title={`NexGen schemas · ${fmtBytes(total)}`}
        subtitle="One schema per service. Views are the published contracts other services read.">
        <div className="space-y-2">
          {own.map(s => <SchemaRow key={s.database} s={s} />)}
        </div>
      </Card>

      {legacy.length > 0 && (
        <Card title="Legacy databases (read only)"
          subtitle="The two original applications' databases. NexGen only reads them, for the import and for comparisons.">
          <div className="space-y-2">
            {legacy.map(s => <SchemaRow key={s.database} s={s} />)}
          </div>
        </Card>
      )}
    </div>
  );
}

function SchemaRow({ s }: { s: SchemaInfo }) {
  const [open, setOpen] = useState(false);
  const tables = s.tables.filter(t => t.kind === 'table');
  return (
    <div className="rounded-lg border border-gray-800 bg-gray-950/40">
      <button onClick={() => setOpen(o => !o)} className="w-full flex flex-wrap items-center gap-x-4 gap-y-1 px-3 py-2 text-sm text-left">
        {open ? <ChevronDown className="w-4 h-4 text-gray-500" /> : <ChevronRight className="w-4 h-4 text-gray-500" />}
        <span className="font-mono text-gray-200">{s.database}</span>
        <span className="text-gray-500">{s.key}</span>
        <span className="text-gray-400 tabular">{fmtBytes(s.bytes)}</span>
        <span className="text-gray-500">{tables.length} tables · {s.views} views</span>
      </button>
      {open && (
        <div className="border-t border-gray-800 px-3 py-2 overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs uppercase tracking-wide text-gray-500">
                <th className="py-1.5 pr-3 font-medium">Table</th>
                <th className="py-1.5 pr-3 font-medium text-right">Rows (est.)</th>
                <th className="py-1.5 pr-3 font-medium text-right">Data</th>
                <th className="py-1.5 pr-3 font-medium text-right">Indexes</th>
                <th className="py-1.5 font-medium" />
              </tr>
            </thead>
            <tbody>
              {tables.map(t => (
                <tr key={t.name} className="border-t border-gray-800/50">
                  <td className="py-1.5 pr-3 font-mono text-gray-300">{t.name}</td>
                  <td className="py-1.5 pr-3 text-right tabular text-gray-300">{fmtInt(t.rows)}</td>
                  <td className="py-1.5 pr-3 text-right tabular text-gray-400">{t.data_mb?.toFixed(1)} MB</td>
                  <td className="py-1.5 pr-3 text-right tabular text-gray-400">{t.index_mb?.toFixed(1)} MB</td>
                  <td className="py-1.5">{t.partitioned && <Badge variant="info">partitioned</Badge>}</td>
                </tr>
              ))}
              {s.tables.filter(t => t.kind === 'view').length > 0 && (
                <tr className="border-t border-gray-800/50">
                  <td colSpan={5} className="py-1.5 text-gray-500">
                    Views: <span className="font-mono">{s.tables.filter(t => t.kind === 'view').map(t => t.name).join(', ')}</span>
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
