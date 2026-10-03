import { createContext, useContext, useMemo, useState, type ReactNode } from 'react';
import { useApi } from '../../../hooks/useApi';
import {
  EMPTY_FILTERS, filtersToParams, getDashMeta,
  type DashboardMeta, type TTAFilters,
} from '../../../services/ttaDashboard';

interface Ctx {
  filters: TTAFilters;
  setFilters: (f: TTAFilters) => void;
  reset: () => void;
  params: Record<string, string>;
  /** stable key for useApi deps — changes whenever any filter changes */
  paramsKey: string;
  meta: DashboardMeta | null;
  metaLoading: boolean;
}

const TTAFilterCtx = createContext<Ctx | null>(null);

export function TTAFilterProvider({ children }: { children: ReactNode }) {
  const [filters, setFilters] = useState<TTAFilters>(EMPTY_FILTERS);
  const { data: meta, loading: metaLoading } = useApi(() => getDashMeta());

  const params = useMemo(() => filtersToParams(filters), [filters]);
  const paramsKey = useMemo(() => JSON.stringify(params), [params]);

  const value = useMemo(() => ({
    filters, setFilters, reset: () => setFilters(EMPTY_FILTERS),
    params, paramsKey, meta, metaLoading,
  }), [filters, params, paramsKey, meta, metaLoading]);

  return <TTAFilterCtx.Provider value={value}>{children}</TTAFilterCtx.Provider>;
}

export function useTTAFilters(): Ctx {
  const ctx = useContext(TTAFilterCtx);
  if (!ctx) throw new Error('useTTAFilters must be used inside TTAFilterProvider');
  return ctx;
}
