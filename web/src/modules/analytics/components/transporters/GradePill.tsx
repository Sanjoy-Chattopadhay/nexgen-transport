const GRADE_STYLES: Record<string, string> = {
  A: 'bg-emerald-900/50 text-emerald-300 border-emerald-700',
  B: 'bg-lime-900/50 text-lime-300 border-lime-700',
  C: 'bg-amber-900/50 text-amber-300 border-amber-700',
  D: 'bg-orange-900/50 text-orange-300 border-orange-700',
  E: 'bg-red-900/50 text-red-300 border-red-700',
};

/** Composite-score grade chip (A best → E worst; "—" for unqualified carriers). */
export default function GradePill({ grade, size = 'sm' }: { grade: string; size?: 'sm' | 'lg' }) {
  const style = GRADE_STYLES[grade] ?? 'bg-gray-800 text-gray-500 border-gray-700';
  const dims = size === 'lg' ? 'w-9 h-9 text-lg' : 'w-6 h-6 text-xs';
  return (
    <span className={`inline-flex items-center justify-center rounded-lg border font-bold ${dims} ${style}`}
      title={grade === '—' ? 'Too few trips to score' : `Grade ${grade}`}>
      {grade}
    </span>
  );
}

/** Shared colour ramp for on-time percentages across the transporter pages. */
export const otdClass = (v: number | null | undefined) =>
  v == null ? 'text-gray-500'
    : v >= 95 ? 'text-emerald-400 font-semibold'
    : v >= 80 ? 'text-amber-400'
    : 'text-red-400 font-semibold';
