import { useState } from 'react';
import { Settings2, Save, X, Loader2 } from 'lucide-react';
import { saveCostConfig, type CostConfig } from '../../services/tta';

const FIELDS: { key: keyof CostConfig; label: string; unit: string; step: string }[] = [
  { key: 'fuel_price_per_liter', label: 'Fuel Price', unit: '₹ / litre', step: '0.5' },
  { key: 'fuel_efficiency_kmpl', label: 'Mileage', unit: 'km / litre', step: '0.1' },
  { key: 'driver_wage_per_hour', label: 'Driver Wage', unit: '₹ / hour', step: '5' },
  { key: 'idle_fuel_consumption_lph', label: 'Idle Burn', unit: 'litre / hour (engine on)', step: '0.1' },
];

/** Inline editor for the journey cost model. Saved values persist in the DB
 * and apply to every trip's analysis + comparison. */
export default function CostConfigPanel({ params, onSaved }: {
  params: CostConfig; onSaved: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [form, setForm] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const open = () => {
    setForm(Object.fromEntries(FIELDS.map(f => [f.key, String(params[f.key])])));
    setError(null);
    setEditing(true);
  };

  const save = async () => {
    const cfg: any = {};
    for (const f of FIELDS) {
      const v = parseFloat(form[f.key]);
      if (isNaN(v) || v <= 0) {
        setError(`${f.label} must be a number greater than 0`);
        return;
      }
      cfg[f.key] = v;
    }
    setSaving(true);
    setError(null);
    try {
      await saveCostConfig(cfg);
      setEditing(false);
      onSaved();
    } catch (e: any) {
      setError(e?.response?.data?.detail || e.message || 'Save failed');
    } finally {
      setSaving(false);
    }
  };

  if (!editing) {
    return (
      <button onClick={open}
        className="flex items-center gap-1.5 px-3 py-1.5 bg-gray-800 hover:bg-gray-700 border border-gray-700 text-gray-300 rounded-lg text-xs font-medium transition-colors">
        <Settings2 className="w-3.5 h-3.5" /> Edit rates
      </button>
    );
  }

  return (
    <div className="w-full bg-gray-800/60 border border-gray-700 rounded-lg p-4 mt-1">
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-3">
        {FIELDS.map(f => (
          <div key={f.key}>
            <label className="block text-xs text-gray-400 mb-1">{f.label}</label>
            <input
              type="number" step={f.step} min="0"
              value={form[f.key] ?? ''}
              onChange={e => setForm(prev => ({ ...prev, [f.key]: e.target.value }))}
              className="w-full bg-gray-900 border border-gray-700 rounded-lg px-3 py-2 text-sm text-gray-100 focus:outline-none focus:border-blue-500"
            />
            <p className="text-xs text-gray-500 mt-0.5">{f.unit}</p>
          </div>
        ))}
      </div>
      {error && <p className="text-red-400 text-xs mb-2">{error}</p>}
      <div className="flex items-center gap-2">
        <button onClick={save} disabled={saving}
          className="flex items-center gap-1.5 px-3 py-1.5 bg-blue-600 hover:bg-blue-700 text-white rounded-lg text-xs font-medium disabled:opacity-50 transition-colors">
          {saving ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Save className="w-3.5 h-3.5" />}
          Save & recalculate
        </button>
        <button onClick={() => setEditing(false)}
          className="flex items-center gap-1.5 px-3 py-1.5 bg-gray-800 hover:bg-gray-700 border border-gray-700 text-gray-400 rounded-lg text-xs font-medium transition-colors">
          <X className="w-3.5 h-3.5" /> Cancel
        </button>
        <span className="text-xs text-gray-500 ml-2">applies to all trips (analysis + comparison), saved in the database</span>
      </div>
    </div>
  );
}
