import { Layers, MapPin, Globe2 } from 'lucide-react';
import { useTripClass } from '../../context/TripClassContext';
import type { TripClassValue } from '../../lib/tripClass';

/**
 * Global zonal/local switch, directly under the Smart-Truck brand.
 *
 * The two upstream eTrans feeds are the app's top-level split, so the control
 * sits above everything else in the sidebar — ahead of the consignor switcher —
 * and every page below refetches when it changes.
 */

const fmt = (n: number) =>
  n >= 1000 ? `${(n / 1000).toFixed(n >= 10000 ? 0 : 1)}k` : String(n);

interface Option {
  value: TripClassValue;
  label: string;
  icon: typeof Layers;
  /** Tailwind classes for the selected state — each class gets its own hue so
   *  the active feed is identifiable at a glance, not just by position. */
  active: string;
  dot: string;
}

const OPTIONS: Option[] = [
  {
    value: null, label: 'All', icon: Layers,
    active: 'bg-gray-700/80 text-white shadow-inner ring-1 ring-gray-500/60',
    dot: 'bg-gray-400',
  },
  {
    value: 'zonal', label: 'Zonal', icon: Globe2,
    active: 'bg-blue-600/90 text-white shadow-lg shadow-blue-900/40 ring-1 ring-blue-400/60',
    dot: 'bg-blue-400',
  },
  {
    value: 'local', label: 'Local', icon: MapPin,
    active: 'bg-emerald-600/90 text-white shadow-lg shadow-emerald-900/40 ring-1 ring-emerald-400/60',
    dot: 'bg-emerald-400',
  },
];

export default function TripClassFilter() {
  const { tripClass, counts, total, loading, setTripClass } = useTripClass();

  const countFor = (value: TripClassValue) =>
    value === null ? total : counts.find(c => c.key === value)?.trips ?? 0;

  const activeLabel =
    tripClass === null ? 'All trip classes'
      : counts.find(c => c.key === tripClass)?.label ?? tripClass;

  return (
    <div className="px-3 pt-3 pb-2 border-b border-gray-800">
      <div className="flex items-center justify-between px-1 mb-1.5">
        <span className="text-xs uppercase tracking-wider text-gray-500 font-semibold">
          Trip class
        </span>
        {!loading && (
          <span className="text-xs text-gray-600 tabular-nums">
            {fmt(countFor(tripClass))} trips
          </span>
        )}
      </div>

      <div
        role="radiogroup"
        aria-label="Trip class"
        className="grid grid-cols-3 gap-1 p-1 bg-gray-950/70 rounded-xl border border-gray-800"
      >
        {OPTIONS.map(opt => {
          const selected = tripClass === opt.value;
          const n = countFor(opt.value);
          return (
            <button
              key={opt.label}
              role="radio"
              aria-checked={selected}
              title={`${opt.label} — ${n.toLocaleString()} trips`}
              onClick={() => setTripClass(opt.value)}
              className={`group relative flex flex-col items-center justify-center gap-0.5 py-2 px-1
                rounded-lg text-xs font-semibold transition-all duration-200
                focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-400
                ${selected
                  ? opt.active
                  : 'text-gray-400 hover:text-gray-100 hover:bg-gray-800/70'}`}
            >
              <opt.icon className={`w-3.5 h-3.5 transition-transform duration-200 ${
                selected ? 'scale-110' : 'group-hover:scale-105'}`} />
              <span className="leading-none">{opt.label}</span>
              <span className={`leading-none tabular-nums text-xs font-medium ${
                selected ? 'text-white/80' : 'text-gray-600 group-hover:text-gray-500'}`}>
                {loading ? '·' : fmt(n)}
              </span>
              {selected && (
                <span className={`absolute -top-px left-1/2 -translate-x-1/2 w-6 h-0.5 rounded-full ${opt.dot}`} />
              )}
            </button>
          );
        })}
      </div>

      <p className="mt-1.5 px-1 text-xs text-gray-600 leading-tight">
        {activeLabel}
        {tripClass && ' · every page below is filtered'}
      </p>
    </div>
  );
}
