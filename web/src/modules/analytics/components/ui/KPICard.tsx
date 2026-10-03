import type { LucideIcon } from 'lucide-react';
import { KPI_STYLES } from '../../lib/colors';
import InfoDot from './InfoDot';
import type { KpiInfo } from '../../lib/kpiInfo';

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
}

export default function KPICard({ label, value, icon: Icon, color, info, onDrill, drillLabel }: Props) {
  const s = KPI_STYLES[color] || KPI_STYLES.blue;
  return (
    <div className={`${s.bg} rounded-xl border border-gray-800 p-5`}>
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="flex items-center gap-1.5 mb-1">
            <p className="text-sm text-gray-400 leading-snug">{label}</p>
            {info && <InfoDot title={label} what={info.what} formula={info.formula} />}
          </div>
          {onDrill ? (
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
    </div>
  );
}
