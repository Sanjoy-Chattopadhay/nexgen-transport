import { Activity, CircleSlash, Clock } from 'lucide-react';
import type { SchedulerState } from '../../services/etlSync';

/**
 * Proof the background worker is alive.
 *
 * This reads the RUNNING APScheduler's own job table (id, trigger, next fire
 * time) rather than the config rows that describe what it should be doing — so
 * a lane whose row says "enabled" but whose job never got registered shows up
 * here as missing instead of silently looking healthy.
 */
export default function SchedulerHealth({ state }: { state: SchedulerState | null }) {
  const alive = !!state?.alive;
  const jobs = state?.jobs || [];

  return (
    <div className="bg-gray-900 rounded-xl border border-gray-800 p-5 mb-5">
      <div className="flex items-center justify-between mb-3 flex-wrap gap-2">
        <div className="flex items-center gap-2">
          {alive
            ? <Activity className="w-5 h-5 text-emerald-400" />
            : <CircleSlash className="w-5 h-5 text-red-400" />}
          <h2 className="text-lg font-semibold text-white">Scheduler</h2>
          <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${
            alive ? 'bg-emerald-900/40 text-emerald-400' : 'bg-red-900/40 text-red-400'}`}>
            {alive ? 'running' : 'not running'}
          </span>
          {alive && (
            <span className="relative flex h-2 w-2" title="Background worker is alive">
              <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75" />
              <span className="relative inline-flex rounded-full h-2 w-2 bg-emerald-500" />
            </span>
          )}
        </div>
        {state?.server_time && (
          <span className="text-xs text-gray-500 flex items-center gap-1">
            <Clock className="w-3 h-3" /> server {new Date(state.server_time).toLocaleTimeString()}
          </span>
        )}
      </div>

      {jobs.length === 0 ? (
        <p className="text-gray-500 text-sm">
          No jobs registered — every lane is disabled or unconfigured.
        </p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs text-gray-500 uppercase tracking-wider border-b border-gray-800">
                <th className="py-2 pr-4 font-medium">Job</th>
                <th className="py-2 pr-4 font-medium">Trigger</th>
                <th className="py-2 pr-4 font-medium">Next fire</th>
                <th className="py-2 pr-4 font-medium text-right">Max instances</th>
                <th className="py-2 font-medium text-right">Coalesce</th>
              </tr>
            </thead>
            <tbody>
              {jobs.map(j => (
                <tr key={j.id} className="border-b border-gray-800/50">
                  <td className="py-2 pr-4">
                    <span className="text-gray-200">{j.name}</span>
                    <span className="block text-xs text-gray-600 font-mono">{j.id}</span>
                  </td>
                  <td className="py-2 pr-4 text-gray-400 font-mono text-xs">{j.trigger}</td>
                  <td className="py-2 pr-4 text-gray-300 whitespace-nowrap">
                    {j.next_run_time ? new Date(j.next_run_time).toLocaleString() : '—'}
                  </td>
                  <td className="py-2 pr-4 text-right text-gray-400">{j.max_instances}</td>
                  <td className="py-2 text-right text-gray-400">{j.coalesce ? 'yes' : 'no'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <p className="mt-3 text-xs text-gray-600 leading-relaxed">
        Each lane has its own job with <code>max_instances=1</code>, so a lane never overlaps
        itself. Lanes DO overlap each other by design — fetching runs concurrently under a shared
        upstream-concurrency cap, while the database write stage is serialized across lanes.
      </p>
    </div>
  );
}
