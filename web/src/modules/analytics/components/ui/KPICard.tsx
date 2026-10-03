import { useId } from 'react';
import type { LucideIcon } from 'lucide-react';
import { ChevronDown } from 'lucide-react';
import { KPI_STYLES } from '../../lib/colors';
import InfoDot from './InfoDot';
import type { KpiInfo } from '../../lib/kpiInfo';
import { useProofGrid, type ProofSpec } from '../../../../core/proof/context';

interface Props {
  label: string;
  value: string | number;
  icon: LucideIcon;
  color: string;
  /** eye-button explainer: what the KPI means + how it's calculated */
  info?: KpiInfo;
  /** if provided, the value becomes clickable and opens a drill-down */
  onDrill?: () => void;
  /** tooltip on the drill-able value */
  drillLabel?: string;
  /**
   * How the figure is calculated, with the records behind it: the whole card
   * opens the proof beneath its ProofGrid (core/proof). Preferred over onDrill.
   */
  proof?: ProofSpec;
}

export default function KPICard({ label, value, icon: Icon, color, info, onDrill, drillLabel, proof }: Props) {
  const s = KPI_STYLES[color] || KPI_STYLES.blue;
  const grid = useProofGrid();
  const id = useId();
  const proofable = !!proof && !!grid;
  const isOpen = proofable && grid!.open?.id === id;

  const body = (
    <div className="flex items-start justify-between gap-2">
      <div className="min-w-0">
        <div className="flex items-center gap-1.5 mb-1">
          <p className="text-sm text-gray-400 leading-snug">{label}</p>
          {info && (
            // The explainer is its own control: it must not also toggle the proof.
            <span onClick={e => e.stopPropagation()} onKeyDown={e => e.stopPropagation()}>
              <InfoDot title={label} what={info.what} formula={info.formula} />
            </span>
          )}
        </div>
        {onDrill && !proofable ? (
          <button onClick={onDrill} title={drillLabel || 'View breakdown'}
            className={`text-2xl font-bold ${s.text} text-left decoration-dotted underline-offset-4 hover:underline`}>
            {value}
          </button>
        ) : (
          <p className={`text-2xl font-bold ${s.text}`}>{value}</p>
        )}
      </div>
      <Icon className={`w-10 h-10 ${s.icon} opacity-60 shrink-0`} />
    </div>
  );

  if (!proofable) {
    return <div className={`${s.bg} rounded-xl border border-gray-800 p-5`}>{body}</div>;
  }
  return (
    // A div with a button role rather than a <button>: the card holds the
    // InfoDot's own button, and buttons cannot nest.
    <div role="button" tabIndex={0} aria-expanded={isOpen}
      title={isOpen ? 'Close' : `How “${label}” is calculated, with the records behind it`}
      onClick={() => grid!.toggle({ id, label, display: String(value), spec: proof! })}
      onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); grid!.toggle({ id, label, display: String(value), spec: proof! }); } }}
      className={`${s.bg} rounded-xl border p-5 relative cursor-pointer transition-colors group ${isOpen
        ? 'border-blue-500/60 ring-1 ring-blue-500/30' : 'border-gray-800 hover:border-gray-700'}`}>
      <ChevronDown className={`w-4 h-4 absolute top-2.5 right-2.5 transition-transform ${isOpen
        ? 'rotate-180 text-blue-400' : 'text-gray-600 group-hover:text-gray-400'}`} />
      {body}
    </div>
  );
}
