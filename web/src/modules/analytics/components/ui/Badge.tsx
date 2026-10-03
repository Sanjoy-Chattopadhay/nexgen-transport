const VARIANTS: Record<string, string> = {
  success: 'bg-emerald-900/40 text-emerald-400',
  warning: 'bg-amber-900/40 text-amber-400',
  danger: 'bg-red-900/40 text-red-400',
  info: 'bg-blue-900/40 text-blue-400',
  neutral: 'bg-gray-800 text-gray-400',
};

export default function Badge({ label, variant = 'neutral' }: { label: string; variant?: keyof typeof VARIANTS }) {
  return <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${VARIANTS[variant] || VARIANTS.neutral}`}>{label}</span>;
}
