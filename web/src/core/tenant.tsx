/**
 * Who is using the app, as the platform service sees it: the tenant (client),
 * its branding and which modules it has switched on.
 *
 * There is no login yet, so this is always the default tenant; the shape is
 * the one a login will fill. If the platform service is stopped, the app
 * falls back to every module on rather than hiding pages.
 */
import { createContext, useContext, useEffect, useState, type ReactNode } from 'react';

export interface TenantInfo {
  tenant: { id: number; code: string; name: string; timezone: string };
  product: string;
  branding: { product_name: string; theme: string; logo: string };
  modules: Record<string, boolean>;
  features: Record<string, boolean>;
  labels: Record<string, string>;
}

const Ctx = createContext<TenantInfo | null>(null);

export function TenantProvider({ children }: { children: ReactNode }) {
  const [info, setInfo] = useState<TenantInfo | null>(null);
  useEffect(() => {
    let alive = true;
    fetch('/api/v1/platform/me')
      .then(r => (r.ok ? r.json() : null))
      .then(d => { if (alive && d) setInfo(d); })
      .catch(() => { /* platform stopped: defaults below */ });
    return () => { alive = false; };
  }, []);
  return <Ctx.Provider value={info}>{children}</Ctx.Provider>;
}

export const useTenant = () => useContext(Ctx);

/** A module is on unless the tenant explicitly switched it off. */
export function useModuleOn(): (module: string | undefined) => boolean {
  const info = useContext(Ctx);
  return module => !module || !info || info.modules?.[module] !== false;
}
