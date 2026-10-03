import { useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { X, ExternalLink } from 'lucide-react';
import Spinner from './Spinner';
import type { DrillConfig } from '../../context/DrillDownContext';

interface Props {
  cfg: DrillConfig;
  rows: any[] | null;
  loading: boolean;
  error: string | null;
  onClose: () => void;
}

/**
 * Left-anchored, half-screen drill-down drawer. Opened whenever a count (e.g.
 * "5 trucks", "12 routes") needs to reveal the actual entities behind it as a
 * clickable table. Rows navigate to their detail page and close the drawer.
 */
export default function DrillDownDrawer({ cfg, rows, loading, error, onClose }: Props) {
  const navigate = useNavigate();

  // Close on Escape.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose]);

  const go = (row: any) => {
    const to = cfg.rowLink?.(row);
    if (to) { onClose(); navigate(to); }
  };

  return (
    <div className="fixed inset-0 z-[2000]">
      {/* backdrop */}
      <div className="absolute inset-0 bg-black/50 backdrop-blur-sm animate-fade-in" onClick={onClose} />
      {/* left half-screen panel */}
      <div className="absolute left-0 top-0 h-full w-full sm:w-1/2 max-w-2xl bg-gray-900 border-r border-gray-800 shadow-2xl flex flex-col animate-slide-in-left">
        <div className="flex items-start justify-between gap-3 px-5 py-4 border-b border-gray-800 shrink-0">
          <div>
            <h2 className="text-base font-semibold text-white">{cfg.title}</h2>
            {cfg.subtitle && <p className="text-xs text-gray-500 mt-0.5">{cfg.subtitle}</p>}
            {rows && !loading && (
              <p className="text-xs text-gray-500 mt-0.5">{rows.length.toLocaleString('en-IN')} rows</p>
            )}
          </div>
          <button onClick={onClose} aria-label="Close"
            className="p-1.5 rounded-lg text-gray-400 hover:text-gray-100 hover:bg-gray-800 transition-colors">
            <X className="w-5 h-5" />
          </button>
        </div>

        <div className="flex-1 overflow-auto">
          {loading ? (
            <div className="p-8"><Spinner /></div>
          ) : error ? (
            <p className="p-6 text-sm text-red-400">{error}</p>
          ) : !rows || rows.length === 0 ? (
            <p className="p-6 text-sm text-gray-500">{cfg.empty || 'No items to show.'}</p>
          ) : (
            <table className="w-full text-sm">
              <thead className="sticky top-0 bg-gray-900 z-10">
                <tr className="border-b border-gray-800">
                  {cfg.columns.map(c => (
                    <th key={c.key}
                      className={`px-4 py-2.5 text-xs uppercase tracking-wide text-gray-500 font-medium ${c.align === 'right' ? 'text-right' : 'text-left'}`}>
                      {c.label}
                    </th>
                  ))}
                  {cfg.rowLink && <th className="w-8" />}
                </tr>
              </thead>
              <tbody>
                {rows.map((row, i) => {
                  const clickable = !!cfg.rowLink?.(row);
                  return (
                    <tr key={i} onClick={() => go(row)}
                      className={`border-b border-gray-800/50 transition-colors ${clickable ? 'cursor-pointer hover:bg-gray-800/60' : ''}`}>
                      {cfg.columns.map(c => (
                        <td key={c.key}
                          className={`px-4 py-2.5 text-gray-300 ${c.align === 'right' ? 'text-right tabular-nums' : 'text-left'}`}>
                          {c.render ? c.render(row) : (row[c.key] ?? '-')}
                        </td>
                      ))}
                      {cfg.rowLink && (
                        <td className="pr-3 text-gray-600">
                          {clickable && <ExternalLink className="w-3.5 h-3.5" />}
                        </td>
                      )}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </div>
  );
}
