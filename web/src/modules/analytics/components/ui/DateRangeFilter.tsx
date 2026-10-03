import { Calendar } from 'lucide-react';
import type { DatePreset } from '../../hooks/useDateRange';

const PRESETS: { key: DatePreset; label: string }[] = [
  { key: '7d', label: '7D' },
  { key: '30d', label: '30D' },
  { key: '90d', label: '90D' },
  { key: 'all', label: 'All' },
];

interface Props {
  from: string;
  to: string;
  preset: DatePreset;
  onPreset: (p: DatePreset) => void;
  onCustom: (from: string, to: string) => void;
  min?: string;
  max?: string;
  /** trailing note, e.g. row count */
  note?: string;
}

/** Calendar date-range filter: rolling presets + two date pickers. Sits above
 *  the charts it controls; the default is a rolling 7-day window. */
export default function DateRangeFilter({ from, to, preset, onPreset, onCustom, min, max, note }: Props) {
  return (
    <div className="bg-gray-900 border border-gray-800 rounded-xl px-4 py-3 mb-6 flex items-center gap-3 flex-wrap">
      <span className="flex items-center gap-1.5 text-xs text-gray-400 font-medium">
        <Calendar className="w-4 h-4 text-blue-400" /> Date range
      </span>
      <div className="flex items-center gap-1">
        {PRESETS.map(p => (
          <button key={p.key} onClick={() => onPreset(p.key)}
            className={`px-2.5 py-1 rounded-lg text-xs font-medium transition-colors ${
              preset === p.key
                ? 'bg-blue-600 text-white'
                : 'bg-gray-800 text-gray-400 hover:text-gray-200 border border-gray-700'
            }`}>{p.label}</button>
        ))}
      </div>
      <div className="flex items-center gap-1.5">
        <input type="date" value={from} min={min} max={max}
          onChange={e => onCustom(e.target.value, to)}
          className="bg-gray-800 border border-gray-700 rounded-lg px-2 py-1 text-xs text-gray-300 outline-none focus:border-blue-600 [color-scheme:dark]" />
        <span className="text-gray-600 text-xs">→</span>
        <input type="date" value={to} min={min} max={max}
          onChange={e => onCustom(from, e.target.value)}
          className="bg-gray-800 border border-gray-700 rounded-lg px-2 py-1 text-xs text-gray-300 outline-none focus:border-blue-600 [color-scheme:dark]" />
      </div>
      <span className="text-xs text-gray-500">
        {preset === 'all' ? 'all time'
          : preset === 'custom' ? 'custom range'
          : `last ${preset.replace('d', '')} days`}
      </span>
      {note && <span className="ml-auto text-xs text-gray-500">{note}</span>}
    </div>
  );
}
