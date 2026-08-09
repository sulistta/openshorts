import React from 'react';
import { AlertTriangle } from 'lucide-react';
import Modal from './Modal';

export default function ConfirmDialog({ isOpen, onClose, onConfirm, title = 'Confirm deletion', description, confirmLabel = 'Delete', busy = false }) {
  return (
    <Modal isOpen={isOpen} onClose={busy ? undefined : onClose} size="sm" eyebrow="This cannot be undone" title={title}>
      <div className="space-y-5">
        <div className="flex items-start gap-3 text-sm leading-relaxed text-ink-2">
          <AlertTriangle size={18} className="mt-0.5 shrink-0 text-warn" />
          <p>{description}</p>
        </div>
        <div className="flex justify-end gap-2">
          <button type="button" onClick={onClose} disabled={busy} className="btn-ghost">Cancel</button>
          <button type="button" onClick={onConfirm} disabled={busy} className="btn-danger">
            {busy ? 'Deleting…' : confirmLabel}
          </button>
        </div>
      </div>
    </Modal>
  );
}
