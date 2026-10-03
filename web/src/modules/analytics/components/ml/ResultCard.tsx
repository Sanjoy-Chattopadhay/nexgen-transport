import { Sparkles } from 'lucide-react';

interface Props {
  children: React.ReactNode;
  title?: string;
  gradient?: string;
  border?: string;
}

export default function ResultCard({
  children,
  title = 'Prediction Result',
  gradient = 'from-blue-900/40 to-indigo-900/40',
  border = 'border-blue-700/30',
}: Props) {
  return (
    <div className={`bg-gradient-to-br ${gradient} rounded-xl border ${border} p-5 animate-scale-in`}>
      <div className="flex items-center gap-2 mb-4">
        <Sparkles className="w-5 h-5 text-blue-400 animate-float" />
        <span className="text-sm font-semibold text-blue-300">{title}</span>
      </div>
      {children}
    </div>
  );
}
