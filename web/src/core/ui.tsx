/**
 * The shell's own small UI kit (the developer page, the sidebar, notices).
 * Same card shape, borders and badge colours as the modules' kits, so the
 * shell and the pages read as one product.
 */
import type { ReactNode } from 'react';
import type { LucideIcon } from 'lucide-react';
import { Loader2 } from 'lucide-react';

export function Card({ title, icon: Icon, actions, children, className = '', subtitle }: {
  title?: ReactNode; icon?: LucideIcon; actions?: ReactNode; children: ReactNode; className?: string; subtitle?: ReactNode;
}) {
  return (
    <section className={`bg-gray-900 rounded-xl border border-gray-800 p-5 ${className}`}>
      {(title || actions) && (
        <div className={`flex items-start justify-between gap-3 ${subtitle ? 'mb-1' : 'mb-4'}`}>
          {title && (
            <h2 className="text-base font-semibold text-white flex items-center gap-2 min-w-0">
              {Icon && <Icon className="w-[18px] h-[18px] shrink-0 text-blue-400" />}
              <span className="truncate">{title}</span>
            </h2>
          )}
          {actions && <div className="flex items-center gap-2 shrink-0 flex-wrap justify-end">{actions}</div>}
        </div>
      )}
      {subtitle && <p className="text-sm text-gray-500 mb-4">{subtitle}</p>}
      {children}
    </section>
  );
}

const BADGE: Record<string, string> = {
  success: 'bg-emerald-900/40 text-emerald-400',
  warning: 'bg-amber-900/40 text-amber-400',
  danger: 'bg-red-900/40 text-red-400',
  info: 'bg-blue-900/40 text-blue-400',
  neutral: 'bg-gray-800 text-gray-400',
};

export type BadgeVariant = keyof typeof BADGE;

export function Badge({ children, variant = 'neutral', title }: { children: ReactNode; variant?: BadgeVariant; title?: string }) {
  return (
    <span title={title}
      className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium whitespace-nowrap ${BADGE[variant] ?? BADGE.neutral}`}>
      {children}
    </span>
  );
}

const BUTTON: Record<string, string> = {
  primary: 'bg-blue-600 text-on-accent hover:bg-blue-500 border-transparent',
  danger: 'bg-red-950/40 text-red-400 border-red-900 hover:bg-red-900/40',
  ghost: 'bg-gray-800/60 text-gray-300 border-gray-700 hover:bg-gray-800 hover:text-white',
};

export function Button({ children, onClick, variant = 'ghost', busy, disabled, title, icon: Icon, small }: {
  children?: ReactNode; onClick?: () => void; variant?: keyof typeof BUTTON; busy?: boolean; disabled?: boolean;
  title?: string; icon?: LucideIcon; small?: boolean;
}) {
  return (
    <button type="button" onClick={onClick} disabled={disabled || busy} title={title}
      className={`inline-flex items-center gap-1.5 rounded-lg border font-medium transition-colors disabled:opacity-40 disabled:cursor-not-allowed ${
        small ? 'px-2 py-1 text-xs' : 'px-3 py-1.5 text-sm'} ${BUTTON[variant]}`}>
      {busy ? <Loader2 className="w-4 h-4 animate-spin" /> : Icon ? <Icon className="w-4 h-4" /> : null}
      {children}
    </button>
  );
}

export function Spinner({ label }: { label?: string }) {
  return (
    <div className="flex items-center justify-center gap-2 py-16 text-gray-500 text-sm">
      <Loader2 className="w-5 h-5 animate-spin" /> {label ?? 'Loading…'}
    </div>
  );
}

export function ErrorNote({ children }: { children: ReactNode }) {
  return (
    <div className="rounded-lg border border-red-900 bg-red-950/30 px-4 py-3 text-sm text-red-300">{children}</div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="text-sm text-gray-500 py-6 text-center">{children}</p>;
}

/** A labelled value in a stat strip. */
export function Stat({ label, value, title }: { label: string; value: ReactNode; title?: string }) {
  return (
    <div className="min-w-0" title={title}>
      <p className="text-xs text-gray-500">{label}</p>
      <p className="text-sm text-gray-200 tabular truncate">{value ?? '—'}</p>
    </div>
  );
}
