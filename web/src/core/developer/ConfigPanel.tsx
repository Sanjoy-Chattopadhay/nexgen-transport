import { useCallback, useEffect, useState } from 'react';
import { RefreshCw, RotateCw } from 'lucide-react';
import { dev } from './api';
import { Button, Card, ErrorNote, Spinner } from '../ui';

/**
 * The effective configuration: config/services.yaml and config/database.yaml
 * after ${VAR} expansion, with every secret masked, plus the gateway's route
 * table. Edit the files, then reload here (the supervisor) and restart a
 * service for it to read them.
 */
export default function ConfigPanel() {
  const [config, setConfig] = useState<any>(null);
  const [routes, setRoutes] = useState<{ pattern: string; service: string }[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [note, setNote] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [c, r] = await Promise.all([dev.config(), dev.routes()]);
      setConfig(c);
      setRoutes(r.routes);
      setError(null);
    } catch (e: any) {
      setError(e?.message || String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  const reload = async () => {
    setNote(null);
    try {
      await dev.reloadConfig();
      setNote('The supervisor re-read the configuration. Restart a service on the Services tab for it to pick up a change.');
      void load();
    } catch (e: any) {
      setNote(`Rejected: ${e?.message || e}`);
    }
  };

  if (error) return <ErrorNote>{error}</ErrorNote>;
  if (!config) return <Spinner />;

  return (
    <div className="space-y-4">
      <Card title="Effective configuration"
        subtitle="config/services.yaml and config/database.yaml with ${VARIABLES} expanded from .env. Secrets are masked."
        actions={(
          <>
            <Button icon={RefreshCw} busy={loading} onClick={load}>Refresh</Button>
            <Button icon={RotateCw} onClick={reload}>Reload files</Button>
          </>
        )}>
        {note && <p className="mb-3 text-sm text-gray-300">{note}</p>}
        <pre className="max-h-[60vh] overflow-auto rounded-lg bg-gray-950 border border-gray-800 p-3 font-mono text-xs leading-relaxed text-gray-300">
          {JSON.stringify(config, null, 2)}
        </pre>
      </Card>

      <Card title="Gateway routes" subtitle="Which service answers which path. The most specific pattern wins.">
        {!routes ? <Spinner /> : (
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-x-6">
            {routes.map(r => (
              <div key={r.pattern} className="flex items-center justify-between gap-3 py-1 border-b border-gray-800/60 text-sm">
                <span className="font-mono text-gray-300 truncate" title={r.pattern}>{r.pattern}</span>
                <span className="text-gray-500 shrink-0">{r.service}</span>
              </div>
            ))}
          </div>
        )}
      </Card>
    </div>
  );
}
