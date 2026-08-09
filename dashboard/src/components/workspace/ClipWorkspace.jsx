import React from 'react';

export default function ClipWorkspace({ children, state = 'idle' }) {
  return (
    <section className="workspace-scroll h-full animate-fade" data-workspace="clip-generator" data-state={state}>
      {children}
    </section>
  );
}
