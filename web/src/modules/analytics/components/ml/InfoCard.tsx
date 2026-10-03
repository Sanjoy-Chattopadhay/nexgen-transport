import { useState } from 'react';
import { ChevronDown, ChevronUp, type LucideIcon } from 'lucide-react';

interface Props {
  title: string;
  icon: LucideIcon;
  iconColor?: string;
  defaultOpen?: boolean;
  children: React.ReactNode;
}

export default function InfoCard({ title, icon: Icon, iconColor = 'text-blue-400', defaultOpen = true, children }: Props) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="bg-gray-900/80 rounded-xl border border-gray-800 overflow-hidden transition-all duration-300">
      <button onClick={() => setOpen(o => !o)}
        className="w-full flex items-center justify-between px-5 py-4 hover:bg-gray-800/50 transition-colors">
        <div className="flex items-center gap-3">
          <Icon className={`w-5 h-5 ${iconColor}`} />
          <span className="text-sm font-semibold text-white">{title}</span>
        </div>
        {open ? <ChevronUp className="w-4 h-4 text-gray-500" /> : <ChevronDown className="w-4 h-4 text-gray-500" />}
      </button>
      <div className={`transition-all duration-300 ease-in-out overflow-hidden ${open ? 'max-h-[2000px] opacity-100' : 'max-h-0 opacity-0'}`}>
        <div className="px-5 pb-5 text-xs text-gray-300 leading-relaxed">
          {children}
        </div>
      </div>
    </div>
  );
}
