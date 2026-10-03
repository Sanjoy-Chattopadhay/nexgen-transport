import { ChevronLeft, ChevronRight } from 'lucide-react';

interface Props {
  page: number;
  totalPages: number;
  onPageChange: (p: number) => void;
}

export default function Pagination({ page, totalPages, onPageChange }: Props) {
  if (totalPages <= 1) return null;

  const pages: number[] = [];
  const start = Math.max(1, page - 2);
  const end = Math.min(totalPages, page + 2);
  for (let i = start; i <= end; i++) pages.push(i);

  return (
    <div className="flex items-center gap-1 mt-4">
      <button onClick={() => onPageChange(page - 1)} disabled={page <= 1}
        className="p-2 rounded-lg text-gray-400 hover:bg-gray-800 disabled:opacity-30 disabled:cursor-not-allowed">
        <ChevronLeft className="w-4 h-4" />
      </button>
      {start > 1 && <span className="px-2 text-gray-500 text-sm">...</span>}
      {pages.map(p => (
        <button key={p} onClick={() => onPageChange(p)}
          className={`px-3 py-1.5 rounded-lg text-sm font-medium ${p === page ? 'bg-blue-600 text-white' : 'text-gray-400 hover:bg-gray-800'}`}>
          {p}
        </button>
      ))}
      {end < totalPages && <span className="px-2 text-gray-500 text-sm">...</span>}
      <button onClick={() => onPageChange(page + 1)} disabled={page >= totalPages}
        className="p-2 rounded-lg text-gray-400 hover:bg-gray-800 disabled:opacity-30 disabled:cursor-not-allowed">
        <ChevronRight className="w-4 h-4" />
      </button>
      <span className="text-xs text-gray-500 ml-2">Page {page} of {totalPages}</span>
    </div>
  );
}
