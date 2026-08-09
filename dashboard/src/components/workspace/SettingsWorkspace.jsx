import React from 'react';

export default function SettingsWorkspace({ children }) {
  return (
    <section className="workspace-scroll h-full animate-fade" data-workspace="settings">
      <div className="mx-auto w-full max-w-3xl p-4 sm:p-8">{children}</div>
    </section>
  );
}
