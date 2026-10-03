import { useEffect, useRef, useState } from 'react';
import { ChevronDown, X } from 'lucide-react';

interface Props {
  label: string;
  options: string[];
  selected: string[];
  onChange: (vals: string[]) => void;
  maxVisible?: number;
}

/** Compact searchable multi-select dropdown (checkbox list). */
export default function MultiSelect({ label, options, selected, onChange, maxVisible = 200 }: Props) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', onDoc);
    return () => document.removeEventListener('mousedown', onDoc);
  }, []);

  const filtered = options
    .filter(o => o.toLowerCase().includes(query.toLowerCase()))
    .slice(0, maxVisible);

  const toggle = (v: string) =>
    onChange(selected.includes(v) ? selected.filter(x => x !== v) : [...selected, v]);

  return (
    <div ref={ref} className="relative">
      <button onClick={() => setOpen(o => !o)}
        className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg border text-xs font-medium transition-colors ${
          selected.length
            ? 'border-blue-600 bg-blue-600/10 text-blue-300'
            : 'border-gray-700 bg-gray-800 text-gray-400 hover:text-gray-200'
        }`}>
        {label}
        {selected.length > 0 && (
          <span className="bg-blue-600 text-white rounded-full px-1.5 text-xs">{selected.length}</span>
        )}
        <ChevronDown className="w-3 h-3" />
      </button>
      {open && (
        <div className="absolute z-[1200] mt-1 w-64 bg-gray-900 border border-gray-700 rounded-lg shadow-xl p-2">
          <div className="flex items-center gap-1 mb-2">
            <input autoFocus value={query} onChange={e => setQuery(e.target.value)}
              placeholder={`Search ${label.toLowerCase()}…`}
              className="flex-1 bg-gray-800 border border-gray-700 rounded px-2 py-1 text-xs text-gray-200 outline-none focus:border-blue-600" />
            {selected.length > 0 && (
              <button onClick={() => onChange([])} title="Clear"
                className="p-1 text-gray-500 hover:text-red-400"><X className="w-3.5 h-3.5" /></button>
            )}
          </div>
          <div className="max-h-56 overflow-y-auto space-y-0.5">
            {filtered.length === 0 && <p className="text-xs text-gray-500 px-1 py-2">No matches</p>}
            {filtered.map(o => (
              <label key={o} className="flex items-center gap-2 px-1.5 py-1 rounded hover:bg-gray-800 cursor-pointer">
                <input type="checkbox" checked={selected.includes(o)} onChange={() => toggle(o)}
                  className="accent-blue-600" />
                <span className="text-xs text-gray-300 truncate" title={o}>{o}</span>
              </label>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
