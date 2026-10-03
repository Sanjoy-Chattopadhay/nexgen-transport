import { ArrowUpDown, ArrowUp, ArrowDown } from 'lucide-react';
import Spinner from './Spinner';

interface ColumnDef<T> {
  key: string;
  label: string;
  sortable?: boolean;
  render?: (row: T) => React.ReactNode;
  className?: string;
}

interface Props<T> {
  columns: ColumnDef<T>[];
  data: T[];
  loading?: boolean;
  sortBy?: string;
  sortOrder?: 'asc' | 'desc';
  onSort?: (col: string) => void;
  onRowClick?: (row: T) => void;
  emptyMessage?: string;
}

export default function DataTable<T extends Record<string, any>>({ columns, data, loading, sortBy, sortOrder, onSort, onRowClick, emptyMessage = 'No data found' }: Props<T>) {
  if (loading) return <Spinner />;

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-gray-800">
            {columns.map(col => (
              <th key={col.key}
                className={`px-4 py-3 text-left text-xs font-medium text-gray-400 uppercase tracking-wider ${col.sortable && onSort ? 'cursor-pointer hover:text-gray-200 select-none' : ''} ${col.className || ''}`}
                onClick={() => col.sortable && onSort?.(col.key)}>
                <div className="flex items-center gap-1">
                  {col.label}
                  {col.sortable && sortBy === col.key ? (
                    sortOrder === 'asc' ? <ArrowUp className="w-3 h-3" /> : <ArrowDown className="w-3 h-3" />
                  ) : col.sortable ? <ArrowUpDown className="w-3 h-3 opacity-30" /> : null}
                </div>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {data.length === 0 ? (
            <tr><td colSpan={columns.length} className="px-4 py-12 text-center text-gray-500">{emptyMessage}</td></tr>
          ) : data.map((row, i) => (
            <tr key={i} onClick={() => onRowClick?.(row)}
              className={`border-b border-gray-800/50 hover:bg-gray-800/50 transition-colors ${onRowClick ? 'cursor-pointer' : ''}`}>
              {columns.map(col => (
                <td key={col.key} className={`px-4 py-3 text-gray-300 ${col.className || ''}`}>
                  {col.render ? col.render(row) : (row[col.key] ?? '-')}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
