import { createContext, useCallback, useContext, useState, type ReactNode } from 'react';
import DrillDownDrawer from '../components/ui/DrillDownDrawer';

export interface DrillColumn {
  key: string;
  label: string;
  align?: 'left' | 'right';
  render?: (row: any) => ReactNode;
}

export interface DrillConfig {
  title: string;
  subtitle?: string;
  columns: DrillColumn[];
  /** route to navigate to when a row is clicked (null = row not clickable) */
  rowLink?: (row: any) => string | null;
  empty?: string;
  /** provide rows directly … */
  rows?: any[];
  /** … or a loader that fetches them (drawer shows a spinner meanwhile) */
  load?: () => Promise<any[]>;
}

interface Ctx {
  /** Open the left half-screen drill-down drawer with a table of rows. */
  open: (cfg: DrillConfig) => void;
  close: () => void;
}

const DrillCtx = createContext<Ctx | null>(null);

/** Mounted once near the app root; any component can call useDrillDown().open(). */
export function DrillDownProvider({ children }: { children: ReactNode }) {
  const [cfg, setCfg] = useState<DrillConfig | null>(null);
  const [rows, setRows] = useState<any[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const open = useCallback((c: DrillConfig) => {
    setCfg(c);
    setError(null);
    if (c.rows) { setRows(c.rows); setLoading(false); return; }
    if (c.load) {
      setRows(null);
      setLoading(true);
      c.load()
        .then(r => setRows(r))
        .catch(e => setError(e?.response?.data?.detail || e?.message || 'Failed to load'))
        .finally(() => setLoading(false));
    } else {
      setRows([]);
    }
  }, []);

  const close = useCallback(() => { setCfg(null); setRows(null); setError(null); }, []);

  return (
    <DrillCtx.Provider value={{ open, close }}>
      {children}
      {cfg && <DrillDownDrawer cfg={cfg} rows={rows} loading={loading} error={error} onClose={close} />}
    </DrillCtx.Provider>
  );
}

export function useDrillDown(): Ctx {
  const ctx = useContext(DrillCtx);
  if (!ctx) throw new Error('useDrillDown must be used within a DrillDownProvider');
  return ctx;
}
