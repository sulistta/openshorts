import { useEffect, useRef } from 'react';
import { X } from 'lucide-react';

function getFocusable(container) {
  return container?.querySelectorAll('button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])') || [];
}

function useModalBehavior(isOpen, onClose, panelRef) {
  const openerRef = useRef(null);

  useEffect(() => {
    if (!isOpen) return undefined;
    openerRef.current = document.activeElement;
    const onKeyDown = (event) => {
      if (event.key === 'Escape') {
        onClose?.();
        return;
      }
      if (event.key !== 'Tab' || !panelRef.current) return;
      const focusable = [...getFocusable(panelRef.current)];
      if (!focusable.length) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    document.addEventListener('keydown', onKeyDown);
    requestAnimationFrame(() => getFocusable(panelRef.current)[0]?.focus());
    return () => {
      document.removeEventListener('keydown', onKeyDown);
      openerRef.current?.focus?.();
    };
  }, [isOpen, onClose, panelRef]);
}


/**
 * The single modal shell for the app (design.md).
 * Plain dark overlay (no backdrop-blur), hairline paper2 panel.
 *
 * Props:
 *  - isOpen / onClose
 *  - title (string, rendered lowercase serif) — optional
 *  - eyebrow (string, mono UPPERCASE micro label above title) — optional
 *  - size: 'sm' | 'md' | 'lg' | 'xl' (max width; default 'md')
 *  - children: body content
 *  - footer: optional node pinned under the body
 *  - hideClose: hide the X button
 */
const SIZES = {
  sm: 'max-w-sm',
  md: 'max-w-md',
  lg: 'max-w-2xl',
  xl: 'max-w-5xl',
};

export default function Modal({ isOpen, onClose, title, eyebrow, size = 'md', children, footer, hideClose = false }) {
  const panelRef = useRef(null);
  useModalBehavior(isOpen, onClose, panelRef);
  if (!isOpen) return null;

  return (
    <div
      className="fixed inset-0 z-[100] flex items-center justify-center bg-black/70 p-4 animate-fade"
      onMouseDown={(e) => { if (e.target === e.currentTarget && onClose) onClose(); }}
      role="dialog"
      aria-modal="true"
      aria-labelledby={title ? 'modal-title' : undefined}
    >
      <div ref={panelRef} className={`card relative w-full ${SIZES[size] || SIZES.md} max-h-[90vh] flex flex-col shadow-popover`}>
        {!hideClose && onClose && (
          <button
            type="button"
            onClick={onClose}
            aria-label="Close dialog"
            className="absolute top-4 right-4 z-10 rounded-control p-1.5 text-muted transition-colors hover:bg-paper-3 hover:text-ink"
          >
            <X size={16} />
          </button>
        )}
        {(title || eyebrow) && (
          <div className="shrink-0 border-b border-rule px-4 pb-4 pt-6 sm:px-6">
            {eyebrow && <p className="eyebrow mb-1.5">{eyebrow}</p>}
            {title && <h2 id="modal-title" className="pr-8 text-xl font-semibold leading-tight text-ink">{title}</h2>}
          </div>
        )}
        <div className="grow overflow-y-auto px-4 py-5 custom-scrollbar sm:px-6">{children}</div>
        {footer && <div className="shrink-0 border-t border-rule px-4 py-4 sm:px-6">{footer}</div>}
      </div>
    </div>
  );
}
