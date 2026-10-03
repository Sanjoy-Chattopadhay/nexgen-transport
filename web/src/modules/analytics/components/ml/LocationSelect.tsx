import { useState, useEffect, useRef } from 'react';
import { MapPin, X } from 'lucide-react';
import { getLocations } from '../../services/locations';

interface Props {
  label: string;
  value: string;
  onChange: (val: string) => void;
  placeholder?: string;
}

export default function LocationSelect({ label, value, onChange, placeholder = 'Search location...' }: Props) {
  const [query, setQuery] = useState(value);
  const [options, setOptions] = useState<{ id: number; name: string }[]>([]);
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const wrapperRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handle = (e: MouseEvent) => {
      if (wrapperRef.current && !wrapperRef.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', handle);
    return () => document.removeEventListener('mousedown', handle);
  }, []);

  useEffect(() => {
    setQuery(value);
  }, [value]);

  const search = async (q: string) => {
    setLoading(true);
    try {
      const res = await getLocations(q, 50);
      setOptions(res.data.locations || []);
    } catch { setOptions([]); }
    setLoading(false);
  };

  const handleInput = (val: string) => {
    setQuery(val);
    setOpen(true);
    search(val);
  };

  const select = (name: string) => {
    setQuery(name);
    onChange(name);
    setOpen(false);
  };

  const clear = () => {
    setQuery('');
    onChange('');
    setOpen(false);
  };

  return (
    <div ref={wrapperRef} className="relative">
      <label className="block text-xs font-medium text-gray-400 mb-1.5">{label}</label>
      <div className="relative">
        <MapPin className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-500" />
        <input
          type="text" value={query} placeholder={placeholder}
          onChange={e => handleInput(e.target.value)}
          onFocus={() => { if (options.length === 0) search(query); setOpen(true); }}
          className="w-full pl-9 pr-8 py-2.5 bg-gray-800 border border-gray-700 rounded-lg text-sm text-white placeholder-gray-500 focus:outline-none focus:border-blue-500/50 focus:ring-1 focus:ring-blue-500/30 transition-all"
        />
        {query && (
          <button onClick={clear} className="absolute right-3 top-1/2 -translate-y-1/2 text-gray-500 hover:text-gray-300">
            <X className="w-3.5 h-3.5" />
          </button>
        )}
      </div>
      {open && (
        <div className="absolute z-50 mt-1 w-full max-h-56 overflow-y-auto bg-gray-800 border border-gray-700 rounded-lg shadow-xl animate-scale-in">
          {loading && <div className="px-3 py-2 text-xs text-gray-500">Searching...</div>}
          {!loading && options.length === 0 && <div className="px-3 py-2 text-xs text-gray-500">No locations found</div>}
          {options.map(o => (
            <button key={o.id} onClick={() => select(o.name)}
              className={`w-full text-left px-3 py-2 text-sm hover:bg-gray-700/60 transition-colors ${
                o.name === value ? 'text-blue-400 bg-blue-500/10' : 'text-gray-300'
              }`}>
              <MapPin className="w-3 h-3 inline mr-2 opacity-40" />{o.name}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
