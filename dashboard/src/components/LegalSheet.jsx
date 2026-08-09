import React from 'react';
import { ExternalLink, Shield } from 'lucide-react';
import Modal from './ui/Modal';
import { openExternal } from '../lib/openExternal';

const LAST_UPDATED = '2026-08-06';

function Section({ title, children }) {
  return (
    <section className="space-y-2">
      <h3 className="text-sm font-semibold text-ink">{title}</h3>
      <div className="space-y-2 text-sm leading-relaxed text-ink-2">{children}</div>
    </section>
  );
}

export default function LegalSheet({ isOpen, onClose }) {
  return (
    <Modal isOpen={isOpen} onClose={onClose} size="lg" eyebrow="Privacy and rights" title="Terms & Privacy">
      <div className="space-y-6">
        <div className="flex items-start gap-3 rounded-input border border-rule bg-paper-3 p-3 text-sm text-ink-2">
          <Shield size={17} className="mt-0.5 shrink-0 text-ok" />
          <p>OpenShorts is local desktop software. Projects stay in your local application data and provider keys are sent only when a requested feature needs them.</p>
        </div>
        <p className="readout">Last updated: {LAST_UPDATED}</p>
        <Section title="Your content and keys">
          <p>Only process videos you own or have permission to use. You are responsible for copyright, privacy, and content sent to third-party providers.</p>
          <p>Provider keys entered in Settings remain in this app's local storage and are sent as request headers when needed.</p>
        </Section>
        <Section title="Local storage and deletion">
          <ul className="list-disc space-y-1 pl-5">
            <li>Projects, clip manifests, and edits are stored in the local application-data directory.</li>
            <li>Projects do not expire automatically; delete them explicitly from Library.</li>
            <li>Temporary uploads and processing files may be cleaned up according to disk limits.</li>
          </ul>
        </Section>
        <Section title="Third-party providers">
          <p>When enabled, Gemini, ElevenLabs, Upload-Post, YouTube, and other integrations receive the data required for the requested operation. Their terms and privacy policies apply.</p>
        </Section>
        <Section title="Contact and changes">
          <p>For project issues and questions, use GitHub Issues.</p>
          <button type="button" onClick={() => openExternal('https://github.com/mutonby/openshorts/issues')} className="btn-ghost text-xs">
            <ExternalLink size={14} /> Open GitHub Issues
          </button>
        </Section>
      </div>
    </Modal>
  );
}
