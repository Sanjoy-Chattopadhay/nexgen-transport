import { useState } from 'react';
import { Play } from 'lucide-react';
import Spinner from '../ui/Spinner';

interface Field {
  name: string;
  label: string;
  type: 'text' | 'number' | 'datetime-local';
  required?: boolean;
  placeholder?: string;
  defaultValue?: string;
}

interface Props {
  fields: Field[];
  onSubmit: (values: Record<string, any>) => void;
  loading: boolean;
  result: any;
  renderResult?: (result: any) => React.ReactNode;
  submitLabel?: string;
}

export default function PredictionForm({ fields, onSubmit, loading, result, renderResult, submitLabel }: Props) {
  const [values, setValues] = useState<Record<string, any>>(() => {
    const defaults: Record<string, any> = {};
    fields.forEach(f => {
      if (f.defaultValue) defaults[f.name] = f.defaultValue;
    });
    return defaults;
  });

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const cleaned: Record<string, any> = {};
    fields.forEach(f => {
      const v = values[f.name];
      if (v !== undefined && v !== '') {
        cleaned[f.name] = f.type === 'number' ? Number(v) : v;
      }
    });
    onSubmit(cleaned);
  };

  return (
    <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
      <form onSubmit={handleSubmit} className="space-y-3">
        {fields.map(f => (
          <div key={f.name}>
            <label className="block text-xs text-gray-400 mb-1">{f.label}{f.required && <span className="text-red-400"> *</span>}</label>
            <input
              type={f.type}
              value={values[f.name] || ''}
              onChange={e => setValues(prev => ({ ...prev, [f.name]: e.target.value }))}
              placeholder={f.placeholder}
              required={f.required}
              step={f.type === 'number' ? 'any' : undefined}
              className="w-full px-3 py-2 bg-gray-800 border border-gray-700 rounded-lg text-sm text-gray-200 focus:outline-none focus:border-blue-500"
            />
          </div>
        ))}
        <button type="submit" disabled={loading}
          className="flex items-center gap-2 px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-lg text-sm font-medium disabled:opacity-50 transition-colors">
          {loading ? <div className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" /> : <Play className="w-4 h-4" />}
          {submitLabel || 'Predict'}
        </button>
      </form>
      <div className="bg-gray-800/50 rounded-xl border border-gray-700 p-4 min-h-[200px]">
        <p className="text-xs text-gray-500 mb-2 uppercase tracking-wider">Result</p>
        {loading ? <Spinner /> : result ? (
          renderResult ? renderResult(result) : (
            <pre className="text-sm text-gray-300 whitespace-pre-wrap overflow-auto max-h-96">{JSON.stringify(result, null, 2)}</pre>
          )
        ) : <p className="text-gray-500 text-sm">Run a prediction to see results</p>}
      </div>
    </div>
  );
}
