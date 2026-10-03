import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Eye } from 'lucide-react';

interface Props {
  /** plain-language description of what the metric means */
  what: string;
  /** how it is calculated (shown in a mono footer) */
  formula?: string;
  /** optional bold heading inside the popover */
  title?: string;
}

const POP_W = 248; // popover width (px)
const MARGIN = 8;  // keep this far from the viewport edge

/**
 * The little "eye" button beside a KPI. Hover or click it to reveal a popover
 * explaining what the KPI means and how it's calculated.
 *
 * The popover is rendered through a portal into <body> with fixed positioning,
 * so it floats above every card/section and is never clipped by a parent's
 * overflow — and it's clamped to the viewport so it can't get cut off left/right.
 */
export default function InfoDot({ what, formula, title }: Props) {
  const [open, setOpen] = useState(false);
  const btnRef = useRef<HTMLButtonElement>(null);
  const popRef = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState<{ top: number; left: number }>({ top: -9999, left: -9999 });

  // Position the portal popover relative to the button, clamped to the viewport.
  useLayoutEffect(() => {
    if (!open) return;
    const place = () => {
      const b = btnRef.current?.getBoundingClientRect();
      if (!b) return;
      let left = b.right - POP_W;                 // right-align to the eye
      left = Math.max(MARGIN, Math.min(left, window.innerWidth - POP_W - MARGIN));
      const popH = popRef.current?.offsetHeight ?? 120;
      let top = b.bottom + 6;                       // prefer below
      if (top + popH > window.innerHeight - MARGIN) // flip above if it would overflow
        top = Math.max(MARGIN, b.top - popH - 6);
      setPos({ top, left });
    };
    place();
    window.addEventListener('scroll', place, true);
    window.addEventListener('resize', place);
    return () => {
      window.removeEventListener('scroll', place, true);
      window.removeEventListener('resize', place);
    };
  }, [open, what, formula]);

  // Close on outside click / Escape.
  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (btnRef.current?.contains(e.target as Node) || popRef.current?.contains(e.target as Node)) return;
      setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false); };
    document.addEventListener('mousedown', onDoc);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDoc);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  return (
    <>
      <button ref={btnRef} type="button"
        aria-label={title ? `What "${title}" means` : 'What this means'}
        onClick={(e) => { e.stopPropagation(); setOpen(o => !o); }}
        onMouseEnter={() => setOpen(true)}
        className="inline-flex text-gray-500 hover:text-blue-400 transition-colors">
        <Eye className="w-3.5 h-3.5" />
      </button>
      {open && createPortal(
        <div ref={popRef} onClick={(e) => e.stopPropagation()}
          style={{ position: 'fixed', top: pos.top, left: pos.left, width: POP_W }}
          className="z-[9999] bg-gray-950 border border-gray-700 rounded-lg shadow-2xl p-3 text-left">
          {title && <p className="text-xs font-semibold text-gray-100 mb-1">{title}</p>}
          <p className="text-xs text-gray-300 leading-relaxed">{what}</p>
          {formula && (
            <p className="text-xs text-gray-500 mt-1.5 pt-1.5 border-t border-gray-800 font-mono leading-relaxed break-words">
              {formula}
            </p>
          )}
        </div>,
        document.body,
      )}
    </>
  );
}
