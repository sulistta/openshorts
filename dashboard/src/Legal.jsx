import React from 'react';
import { ArrowLeft } from 'lucide-react';

const LAST_UPDATED = '2026-08-06';
const ISSUES_URL = 'https://github.com/mutonby/openshorts/issues';

function Section({ title, children }) {
  return (
    <section className="mb-10">
      <h2 className="font-display lowercase text-xl text-ink mb-3">{title}</h2>
      <div className="text-ink2 leading-relaxed space-y-3 text-sm">{children}</div>
    </section>
  );
}

function A({ href, children, external }) {
  return <a className="underline underline-offset-2 hover:text-brass transition-colors" href={href}
    {...(external ? { target: '_blank', rel: 'noopener noreferrer' } : {})}>{children}</a>;
}

export default function Legal() {
  return (
    <div className="min-h-screen bg-paper text-ink2">
      <header className="border-b border-rule sticky top-0 bg-paper z-10">
        <div className="max-w-[65ch] mx-auto px-6 py-3 flex items-center">
          <button onClick={() => { window.location.hash = ''; }} className="btn-quiet">
            <ArrowLeft size={16} /> Back
          </button>
        </div>
      </header>
      <main className="max-w-[65ch] mx-auto px-6 py-12">
        <h1 className="font-display lowercase text-3xl md:text-4xl text-ink mb-3">Terms &amp; Privacy</h1>
        <p className="readout mb-12">Last updated: {LAST_UPDATED}</p>

        <Section title="The short version">
          <p>OpenShorts is local desktop software. It runs its API on your computer, lets you choose the provider keys you use, and keeps projects under your local application data.</p>
          <p>This edition does not create user accounts or apply automatic retention to durable projects.</p>
        </Section>

        <Section title="Your content and keys">
          <p>Only process videos you own or have permission to use. You are responsible for copyright, privacy, and any content sent to third-party providers.</p>
          <p>Provider keys entered in the dashboard stay in the browser and are sent as request headers when needed. Keys such as <code>GEMINI_API_KEY</code> can also be configured in the local application environment.</p>
        </Section>

        <Section title="Local storage and deletion">
          <ul className="list-disc pl-6 space-y-2">
            <li>Projects, clip manifests, and edits are stored in OpenShorts' local application-data directory.</li>
            <li>Projects do not expire automatically. Use the dashboard or DELETE API endpoint for explicit deletion.</li>
            <li>Temporary uploads and transient processing files may be cleaned up according to your disk limits.</li>
            <li>Access logs and backups remain under your local control.</li>
          </ul>
        </Section>

        <Section title="Third-party providers">
          <p>When enabled, Gemini, ElevenLabs, Upload-Post, YouTube, and other integrations receive the data required for the requested operation. Their terms and privacy policies apply to those requests.</p>
        </Section>

        <Section title="No warranty">
          <p>The software is provided as-is, without a guarantee of uptime, accuracy, or fitness for a particular purpose. You are responsible for local backups and access to your computer.</p>
        </Section>

        <Section title="Contact and changes">
          <p>For project issues and questions, use <A href={ISSUES_URL} external>GitHub Issues</A>. This document may change with the software.</p>
        </Section>
      </main>
    </div>
  );
}
