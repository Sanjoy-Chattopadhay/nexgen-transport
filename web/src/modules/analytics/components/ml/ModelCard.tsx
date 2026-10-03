import { Brain, CheckCircle, XCircle } from 'lucide-react';
import Badge from '../ui/Badge';

interface MLModel {
  model_name: string;
  version: number;
  model_type: string;
  metrics: Record<string, number>;
  training_data_count: number;
  is_active: number;
  trained_at: string | null;
}

export default function ModelCard({ model }: { model: MLModel }) {
  const topMetrics = Object.entries(model.metrics || {}).slice(0, 3);
  return (
    <div className="bg-gray-900 rounded-xl border border-gray-800 p-5">
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <Brain className="w-5 h-5 text-purple-400" />
          <h3 className="font-semibold text-gray-200">{model.model_name.replace(/_/g, ' ')}</h3>
        </div>
        {model.is_active ? <CheckCircle className="w-4 h-4 text-emerald-400" /> : <XCircle className="w-4 h-4 text-gray-600" />}
      </div>
      <div className="space-y-2 text-sm">
        <div className="flex justify-between"><span className="text-gray-500">Type</span><span className="text-gray-300">{model.model_type}</span></div>
        <div className="flex justify-between"><span className="text-gray-500">Version</span><Badge label={`v${model.version}`} variant="info" /></div>
        <div className="flex justify-between"><span className="text-gray-500">Training data</span><span className="text-gray-300">{model.training_data_count?.toLocaleString()}</span></div>
        {model.trained_at && <div className="flex justify-between"><span className="text-gray-500">Trained</span><span className="text-gray-300">{new Date(model.trained_at).toLocaleDateString()}</span></div>}
      </div>
      {topMetrics.length > 0 && (
        <div className="mt-3 pt-3 border-t border-gray-800 grid grid-cols-3 gap-2">
          {topMetrics.map(([k, v]) => (
            <div key={k} className="text-center">
              <p className="text-xs text-gray-500">{k}</p>
              <p className="text-sm font-semibold text-blue-400">{typeof v === 'number' ? v.toFixed(2) : v}</p>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
