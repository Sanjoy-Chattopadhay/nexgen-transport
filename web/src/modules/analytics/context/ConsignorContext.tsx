import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from 'react';
import { parseConsignorPath, consignorUrl } from '../lib/consignor';
import { setActiveConsignorId } from '../services/api';
import { listConsignors, type Consignor } from '../services/consignors';

interface ConsignorContextValue {
  /** Active consignor id (from the URL), or null for "all consignors". */
  consignorId: number | null;
  /** Resolved active consignor record, when known. */
  consignor: Consignor | null;
  /** All consignors (for the switcher). */
  consignors: Consignor[];
  /** True when a consignor id is in the URL but not a known consignor. */
  invalid: boolean;
  loading: boolean;
  /** Navigate to the same page scoped to another consignor (null = all). */
  switchConsignor: (id: number | null) => void;
}

const ConsignorContext = createContext<ConsignorContextValue | undefined>(undefined);

export function ConsignorProvider({ children }: { children: ReactNode }) {
  const parsed = parseConsignorPath();
  const consignorId = parsed.consignorId ? Number(parsed.consignorId) : null;

  const [consignors, setConsignors] = useState<Consignor[]>([]);
  const [loading, setLoading] = useState(true);

  // Keep the API layer's scope in sync with the URL (set at import too).
  useEffect(() => { setActiveConsignorId(parsed.consignorId); }, [parsed.consignorId]);

  useEffect(() => {
    listConsignors()
      .then(res => setConsignors(res.data.data))
      .catch(() => setConsignors([]))
      .finally(() => setLoading(false));
  }, []);

  const consignor = useMemo(
    () => consignors.find(c => c.id === consignorId) ?? null,
    [consignors, consignorId],
  );
  const invalid = consignorId != null && !loading && consignor == null;

  const switchConsignor = (id: number | null) => {
    window.location.assign(consignorUrl(id));
  };

  const value: ConsignorContextValue = {
    consignorId, consignor, consignors, invalid, loading, switchConsignor,
  };
  return <ConsignorContext.Provider value={value}>{children}</ConsignorContext.Provider>;
}

export function useConsignor() {
  const ctx = useContext(ConsignorContext);
  if (!ctx) throw new Error('useConsignor must be used within a ConsignorProvider');
  return ctx;
}
