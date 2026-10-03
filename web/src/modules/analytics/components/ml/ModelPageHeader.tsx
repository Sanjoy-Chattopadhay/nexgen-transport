import { ArrowLeft, type LucideIcon } from 'lucide-react';
import { useNavigate } from 'react-router-dom';

interface Props {
  title: string;
  subtitle: string;
  icon: LucideIcon;
  iconColor: string;       // e.g. 'text-blue-400'
  gradientFrom: string;    // e.g. 'from-blue-600/20'
  gradientTo: string;      // e.g. 'to-indigo-600/20'
  accentBorder: string;    // e.g. 'border-blue-500/30'
  accuracy?: string;
  status?: 'active' | 'inactive';
}

export default function ModelPageHeader({
  title, subtitle, icon: Icon, iconColor, gradientFrom, gradientTo, accentBorder,
  accuracy, status = 'active',
}: Props) {
  const navigate = useNavigate();
  return (
    <div className="animate-fade-in-up mb-8">
      <button onClick={() => navigate('/ml')}
        className="flex items-center gap-2 text-gray-400 hover:text-white mb-4 text-sm transition-colors">
        <ArrowLeft className="w-4 h-4" /> Back to ML Hub
      </button>
      <div className={`bg-gradient-to-r ${gradientFrom} ${gradientTo} rounded-2xl border ${accentBorder} p-6 relative overflow-hidden`}>
        {/* Background floating icon */}
        <Icon className={`absolute right-6 top-1/2 -translate-y-1/2 w-28 h-28 ${iconColor} opacity-[0.07] animate-float`} />
        <div className="relative z-10 flex items-start gap-4">
          <div className={`w-14 h-14 rounded-xl bg-gray-900/60 border border-gray-700/50 flex items-center justify-center shrink-0`}>
            <Icon className={`w-7 h-7 ${iconColor}`} />
          </div>
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-3 mb-1">
              <h1 className="text-2xl font-bold text-white">{title}</h1>
              {status === 'active' && (
                <span className="px-2 py-0.5 text-xs font-semibold uppercase tracking-wider bg-emerald-500/20 text-emerald-400 border border-emerald-500/30 rounded-full">
                  Active
                </span>
              )}
              {accuracy && (
                <span className="px-2 py-0.5 text-xs font-semibold uppercase tracking-wider bg-blue-500/20 text-blue-400 border border-blue-500/30 rounded-full">
                  {accuracy}
                </span>
              )}
            </div>
            <p className="text-sm text-gray-400 max-w-2xl">{subtitle}</p>
          </div>
        </div>
      </div>
    </div>
  );
}
