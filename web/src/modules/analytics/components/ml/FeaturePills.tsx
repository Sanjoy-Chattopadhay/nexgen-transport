import { Cpu } from 'lucide-react';

interface Feature {
  name: string;
  description: string;
  importance?: 'high' | 'medium' | 'low';
}

interface Props {
  features: Feature[];
  title?: string;
}

const IMP_COLORS = {
  high: 'bg-blue-500/20 text-blue-400 border-blue-500/30',
  medium: 'bg-gray-700/40 text-gray-300 border-gray-600/40',
  low: 'bg-gray-800/40 text-gray-500 border-gray-700/30',
};

export default function FeaturePills({ features, title = 'Features Used by This Model' }: Props) {
  return (
    <div>
      <div className="flex items-center gap-2 mb-3">
        <Cpu className="w-4 h-4 text-blue-400" />
        <span className="text-xs font-semibold text-gray-400 uppercase tracking-wider">{title}</span>
      </div>
      <div className="flex flex-wrap gap-2">
        {features.map((f, i) => (
          <span key={i} title={f.description}
            className={`inline-flex items-center px-3 py-1.5 text-xs rounded-lg border cursor-default transition-all hover:scale-105 ${
              IMP_COLORS[f.importance || 'medium']
            }`}>
            {f.name}
          </span>
        ))}
      </div>
    </div>
  );
}
