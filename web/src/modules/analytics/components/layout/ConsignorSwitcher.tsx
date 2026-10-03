import { useEffect, useRef, useState } from 'react';
import { Building2, Check, ChevronDown, Globe, AlertTriangle } from 'lucide-react';
import { useConsignor } from '../../context/ConsignorContext';

/** Route-based consignor selector shown in the sidebar. */
export default function ConsignorSwitcher() {
  const { consignorId, consignor, consignors, invalid, switchConsignor } = useConsignor();
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

  const label = consignorId == null ? 'All consignors' : consignor?.name ?? `Consignor ${consignorId}`;
  const filtered = consignors.filter(c => c.name.toLowerCase().includes(query.toLowerCase()));

  return (
    <div ref={ref} className="relative px-3 pt-3">
      <button onClick={() => setOpen(o => !o)}
        className={`w-full flex items-center gap-2 px-3 py-2 rounded-lg border text-left transition-colors ${
          invalid ? 'border-red-700 bg-red-950/30' : 'border-gray-700 bg-gray-800/60 hover:border-gray-600'}`}>
        {invalid ? <AlertTriangle className="w-4 h-4 text-red-400 shrink-0" />
          : consignorId == null ? <Globe className="w-4 h-4 text-gray-400 shrink-0" />
          : <Building2 className="w-4 h-4 text-blue-400 shrink-0" />}
        <div className="min-w-0 flex-1">
          <p className="text-xs uppercase tracking-wide text-gray-500 leading-none">Consignor</p>
          <p className={`text-xs font-semibold truncate mt-0.5 ${invalid ? 'text-red-400' : 'text-white'}`}
            title={label}>{invalid ? `Unknown (${consignorId})` : label}</p>
        </div>
        <ChevronDown className="w-3.5 h-3.5 text-gray-500 shrink-0" />
      </button>

      {open && (
        <div className="absolute z-[1200] left-3 right-3 mt-1 bg-gray-900 border border-gray-700 rounded-lg shadow-xl p-2">
          {consignors.length > 6 && (
            <input autoFocus value={query} onChange={e => setQuery(e.target.value)} placeholder="Search consignor…"
              className="w-full mb-2 bg-gray-800 border border-gray-700 rounded px-2 py-1 text-xs text-gray-200 outline-none focus:border-blue-600" />
          )}
          <div className="max-h-64 overflow-y-auto space-y-0.5">
            <button onClick={() => switchConsignor(null)}
              className="w-full flex items-center gap-2 px-2 py-1.5 rounded hover:bg-gray-800 text-left">
              <Globe className="w-3.5 h-3.5 text-gray-400" />
              <span className="text-xs text-gray-200 flex-1">All consignors</span>
              {consignorId == null && <Check className="w-3.5 h-3.5 text-blue-400" />}
            </button>
            {filtered.map(c => (
              <button key={c.id} onClick={() => switchConsignor(c.id)}
                className="w-full flex items-center gap-2 px-2 py-1.5 rounded hover:bg-gray-800 text-left">
                <Building2 className="w-3.5 h-3.5 text-blue-400 shrink-0" />
                <span className="text-xs text-gray-200 flex-1 truncate" title={c.name}>{c.name}</span>
                <span className="text-xs text-gray-500">{c.trip_count}</span>
                {consignorId === c.id && <Check className="w-3.5 h-3.5 text-blue-400" />}
              </button>
            ))}
            {filtered.length === 0 && <p className="text-xs text-gray-500 px-2 py-2">No matches</p>}
          </div>
        </div>
      )}
    </div>
  );
}
