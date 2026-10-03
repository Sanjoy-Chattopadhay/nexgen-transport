import { tc } from '../../../../core/theme';
interface Stage { stage: string; count: number; }

/** Trip lifecycle funnel — horizontal bars with % of initial stage. */
export default function FunnelStages({ stages }: { stages: Stage[] }) {
  const first = stages[0]?.count || 1;
  const colors = [tc('#3b82f6'), tc('#06b6d4'), tc('#10b981'), '#84cc16', tc('#f59e0b'), '#8b5cf6'];
  return (
    <div className="space-y-2">
      {stages.map((s, i) => {
        const pct = first ? (100 * s.count) / first : 0;
        return (
          <div key={s.stage} className="flex items-center gap-3">
            <span className="w-44 text-xs text-gray-400 text-right shrink-0">{s.stage}</span>
            <div className="flex-1 bg-gray-800/60 rounded h-7 relative overflow-hidden">
              <div className="h-full rounded transition-all"
                style={{ width: `${Math.max(pct, 1)}%`, background: colors[i % colors.length], opacity: 0.75 }} />
              <span className="absolute inset-y-0 left-2 flex items-center text-xs font-semibold text-white">
                {s.count.toLocaleString('en-IN')}
              </span>
              <span className="absolute inset-y-0 right-2 flex items-center text-xs text-gray-300">
                {pct.toFixed(1)}%
              </span>
            </div>
          </div>
        );
      })}
    </div>
  );
}
