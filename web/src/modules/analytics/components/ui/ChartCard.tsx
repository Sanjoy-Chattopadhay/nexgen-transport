import { useState } from 'react';
import type { LucideIcon } from 'lucide-react';
import type { ReactNode } from 'react';
import { ChevronDown, ChevronRight, Sigma } from 'lucide-react';
import { PLOT_NOTES, type FigureMethod } from '../../lib/figureNotes';

interface Props {
  title: string;
  icon?: LucideIcon;
  iconColor?: string;
  explain?: string;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
  /**
   * How this figure is built: its formula, plus which shared plotting note
   * applies. Renders as a collapsed box under the chart.
   */
  method?: FigureMethod;
}

/**
 * Standard dashboard card: title, one-line explainer, chart body, and an
 * optional "How this is built" box beneath.
 *
 * The explainer above says what the chart is FOR in a sentence. The method box
 * below answers what that sentence cannot: what the arithmetic actually is, how
 * the plotting works, and how to read the shape without drawing the wrong
 * conclusion.
 *
 * Collapsed by default, so a reader who already knows the chart is not made to
 * scroll past a paragraph every time — but under the figure rather than in a
 * tooltip, so it can be read alongside the thing it describes and survives a
 * screenshot or a print.
 */
export default function ChartCard({
  title, icon: Icon, iconColor = 'text-blue-400', explain, actions, children,
  className = '', method,
}: Props) {
  const [open, setOpen] = useState(false);
  const note = method ? PLOT_NOTES[method.plot] : null;

  return (
    <div className={`bg-gray-900 rounded-xl border border-gray-800 p-5 ${className}`}>
      <div className="flex items-start justify-between gap-3 mb-1">
        <h2 className="text-base font-semibold text-white flex items-center gap-2">
          {Icon && <Icon className={`w-[18px] h-[18px] ${iconColor}`} />} {title}
        </h2>
        {actions}
      </div>
      {explain && <p className="text-xs text-gray-500 mb-4">{explain}</p>}
      {!explain && <div className="mb-3" />}
      {children}

      {method && note && (
        <div className="mt-4 border-t border-gray-800 pt-3">
          <button
            onClick={() => setOpen(o => !o)}
            aria-expanded={open}
            className="flex items-center gap-1.5 text-xs font-medium text-gray-500 hover:text-gray-300 transition-colors">
            {open ? <ChevronDown className="w-3.5 h-3.5" /> : <ChevronRight className="w-3.5 h-3.5" />}
            <Sigma className="w-3.5 h-3.5" />
            How this is built — formula, plotting and how to read it
          </button>
          {open && (
            <div className="mt-2.5 space-y-2.5 bg-gray-950/60 border border-gray-800 rounded-lg p-3">
              <div>
                <p className="text-xs font-semibold uppercase tracking-wider text-gray-500 mb-1">
                  The number
                </p>
                <p className="text-xs text-gray-300 leading-relaxed">{method.formula}</p>
              </div>
              <div>
                <p className="text-xs font-semibold uppercase tracking-wider text-gray-500 mb-1">
                  How it is plotted
                </p>
                <p className="text-xs text-gray-400 leading-relaxed">{note.plotted}</p>
              </div>
              <div>
                <p className="text-xs font-semibold uppercase tracking-wider text-gray-500 mb-1">
                  How to read it
                </p>
                <p className="text-xs text-gray-400 leading-relaxed">{note.read}</p>
              </div>
              {method.caveat && (
                <div>
                  <p className="text-xs font-semibold uppercase tracking-wider text-amber-600/80 mb-1">
                    Watch out
                  </p>
                  <p className="text-xs text-amber-200/70 leading-relaxed">{method.caveat}</p>
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
