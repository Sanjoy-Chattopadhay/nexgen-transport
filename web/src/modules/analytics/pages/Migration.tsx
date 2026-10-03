import { useState } from 'react';
import { Database, Play, CheckCircle, XCircle } from 'lucide-react';
import PageContainer from '../components/layout/PageContainer';
import ProgressBar from '../components/ui/ProgressBar';
import Spinner from '../components/ui/Spinner';
import { useApi } from '../hooks/useApi';
import { usePolling } from '../hooks/usePolling';
import { createSchema, startTripMigration, migrateWaypoints, refreshSummaries, getMigrationStatus, getMigrationProgress } from '../services/migration';
import { formatNumber } from '../lib/formatters';
import type { MigrationStatus, MigrationProgress } from '../types/migration';

const STEPS = [
  { key: 'schema', title: 'Create Schema', desc: 'Initialize database tables', fn: createSchema },
  { key: 'trips', title: 'Migrate Trips', desc: 'Load CSV trip data into MySQL', fn: startTripMigration },
  { key: 'waypoints', title: 'Migrate Waypoints', desc: 'Load waypoint XLS files', fn: migrateWaypoints },
  { key: 'summaries', title: 'Refresh Summaries', desc: 'Rebuild summary tables', fn: refreshSummaries },
];

export default function Migration() {
  const { data: status, loading: statusLoading, refetch: refetchStatus } = useApi<MigrationStatus>(() => getMigrationStatus());
  const [stepLoading, setStepLoading] = useState<Record<string, boolean>>({});
  const [stepResults, setStepResults] = useState<Record<string, { ok: boolean; msg: string }>>({});
  const [polling, setPolling] = useState(false);

  const progress = usePolling<MigrationProgress>(
    () => getMigrationProgress(),
    2000,
    polling
  );

  const runStep = async (key: string, fn: () => Promise<any>) => {
    setStepLoading(prev => ({ ...prev, [key]: true }));
    setStepResults(prev => { const n = { ...prev }; delete n[key]; return n; });
    if (key === 'trips') setPolling(true);
    try {
      const res = await fn();
      setStepResults(prev => ({ ...prev, [key]: { ok: true, msg: JSON.stringify(res.data).slice(0, 200) } }));
      refetchStatus();
    } catch (err: any) {
      setStepResults(prev => ({ ...prev, [key]: { ok: false, msg: err?.response?.data?.detail || err.message || 'Failed' } }));
    } finally {
      setStepLoading(prev => ({ ...prev, [key]: false }));
      if (key === 'trips') setPolling(false);
    }
  };

  return (
    <PageContainer>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-6">
        {STEPS.map((step, i) => (
          <div key={step.key} className="bg-gray-900 rounded-xl border border-gray-800 p-5">
            <div className="flex items-start justify-between mb-3">
              <div className="flex items-center gap-3">
                <div className="w-8 h-8 bg-blue-600/20 rounded-lg flex items-center justify-center text-blue-400 text-sm font-bold">{i + 1}</div>
                <div>
                  <h3 className="font-semibold text-gray-200">{step.title}</h3>
                  <p className="text-xs text-gray-500">{step.desc}</p>
                </div>
              </div>
              <button
                onClick={() => runStep(step.key, step.fn)}
                disabled={stepLoading[step.key]}
                className="flex items-center gap-1.5 px-3 py-1.5 bg-blue-600 hover:bg-blue-700 text-white rounded-lg text-xs font-medium disabled:opacity-50 transition-colors">
                {stepLoading[step.key] ? <div className="w-3 h-3 border-2 border-white/30 border-t-white rounded-full animate-spin" /> : <Play className="w-3 h-3" />}
                Run
              </button>
            </div>
            {stepResults[step.key] && (
              <div className={`mt-2 flex items-start gap-2 text-xs rounded-lg p-2 ${stepResults[step.key].ok ? 'bg-emerald-900/30 text-emerald-400' : 'bg-red-900/30 text-red-400'}`}>
                {stepResults[step.key].ok ? <CheckCircle className="w-3.5 h-3.5 mt-0.5 shrink-0" /> : <XCircle className="w-3.5 h-3.5 mt-0.5 shrink-0" />}
                <span className="break-all">{stepResults[step.key].msg}</span>
              </div>
            )}
          </div>
        ))}
      </div>

      {progress && progress.running && (
        <div className="bg-gray-900 rounded-xl border border-gray-800 p-5 mb-6">
          <h2 className="text-lg font-semibold text-white mb-4">Live Progress</h2>
          <div className="space-y-3">
            <div className="flex justify-between text-sm">
              <span className="text-gray-400">Phase</span>
              <span className="text-gray-200 font-medium">{progress.phase}</span>
            </div>
            <ProgressBar percent={progress.percent} label="Progress" color="bg-blue-500" />
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4 text-sm">
              <div><span className="text-gray-500 block">Processed</span><span className="text-gray-200">{formatNumber(progress.processed_rows)} / {formatNumber(progress.total_rows)}</span></div>
              <div><span className="text-gray-500 block">Inserted</span><span className="text-emerald-400">{formatNumber(progress.inserted)}</span></div>
              <div><span className="text-gray-500 block">Skipped</span><span className="text-amber-400">{formatNumber(progress.skipped)}</span></div>
              <div><span className="text-gray-500 block">Elapsed</span><span className="text-gray-200">{Math.round(progress.elapsed_seconds)}s</span></div>
            </div>
            {progress.error && <p className="text-red-400 text-sm mt-2">{progress.error}</p>}
            {Object.keys(progress.dimensions || {}).length > 0 && (
              <div className="mt-3 pt-3 border-t border-gray-800">
                <p className="text-xs text-gray-500 uppercase tracking-wider mb-2">Dimensions Loaded</p>
                <div className="flex flex-wrap gap-3 text-sm">
                  {Object.entries(progress.dimensions).map(([k, v]) => (
                    <span key={k} className="text-gray-300">{k}: <span className="text-blue-400 font-medium">{formatNumber(v as number)}</span></span>
                  ))}
                </div>
              </div>
            )}
          </div>
        </div>
      )}

      <div className="bg-gray-900 rounded-xl border border-gray-800 p-5">
        <div className="flex items-center gap-2 mb-4">
          <Database className="w-5 h-5 text-blue-400" />
          <h2 className="text-lg font-semibold text-white">Table Status</h2>
        </div>
        {statusLoading ? <Spinner /> : status ? (
          <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-5 gap-3">
            {Object.entries(status).map(([table, count]) => (
              <div key={table} className="bg-gray-800/50 rounded-lg p-3">
                <p className="text-xs text-gray-500 mb-1">{table}</p>
                <p className="text-lg font-bold text-gray-200">{formatNumber(count)}</p>
              </div>
            ))}
          </div>
        ) : <p className="text-gray-500 text-sm">No data</p>}
      </div>
    </PageContainer>
  );
}
