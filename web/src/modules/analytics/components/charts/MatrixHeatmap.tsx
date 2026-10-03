import { Fragment } from 'react';
import { scaleColor, type ColorScheme } from '../../lib/colorScales';
import { tc } from '../../../../core/theme';

interface Props {
  rows: string[];
  cols: (string | number)[];
  values: (number | null)[][];
  scheme?: ColorScheme;
  /** for 'rdbu' (correlation) values are mapped from [-1,1]; otherwise min-max */
  diverging?: boolean;
  cellW?: number;
  cellH?: number;
  labelW?: number;
  valueFormatter?: (v: number) => string;
}

/** Generic rows × cols heatmap (CSS grid) with tooltips + in-cell values. */
export default function MatrixHeatmap({
  rows, cols, values, scheme = 'blues', diverging = false,
  cellW = 46, cellH = 30, labelW = 180, valueFormatter,
}: Props) {
  if (!rows.length) return <p className="text-gray-500 text-sm py-6">No data for this selection</p>;

  const flat = values.flat().filter((v): v is number => v != null && isFinite(v));
  const lo = flat.length ? Math.min(...flat) : 0;
  const hi = flat.length ? Math.max(...flat) : 1;

  const color = (v: number | null) => {
    if (v == null || !isFinite(v)) return tc('#1f2937');
    const t = diverging ? (v + 1) / 2 : hi === lo ? 0.5 : (v - lo) / (hi - lo);
    return scaleColor(t, scheme);
  };

  const fmt = valueFormatter ?? ((v: number) => (Math.abs(v) >= 100 ? v.toFixed(0) : v.toFixed(1)));

  return (
    <div className="overflow-x-auto">
      <div className="inline-grid gap-0.5"
        style={{ gridTemplateColumns: `${labelW}px repeat(${cols.length}, ${cellW}px)` }}>
        <div />
        {cols.map(c => (
          <div key={String(c)} className="text-center text-xs text-gray-500 pb-1 truncate" title={String(c)}>
            {String(c)}
          </div>
        ))}
        {rows.map((r, ri) => (
          <Fragment key={r}>
            <div className="text-xs text-gray-400 flex items-center pr-2 truncate" title={r}>
              {r.length > Math.floor(labelW / 7) ? r.slice(0, Math.floor(labelW / 7)) + '…' : r}
            </div>
            {cols.map((c, ci) => {
              const v = values[ri]?.[ci] ?? null;
              return (
                <div key={`${r}-${String(c)}`}
                  className="rounded-sm flex items-center justify-center"
                  style={{ width: cellW, height: cellH, background: color(v), opacity: v == null ? 0.4 : 0.85 }}
                  title={`${r} × ${c}: ${v ?? '—'}`}>
                  {v != null && cellW >= 34 && (
                    <span className="text-xs font-medium" style={{ color: '#0b0f19' }}>{fmt(v)}</span>
                  )}
                </div>
              );
            })}
          </Fragment>
        ))}
      </div>
    </div>
  );
}
