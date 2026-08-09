import React, { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import './index.css';
import App from './App.jsx';

async function configureApi() {
  if (!window.__TAURI_INTERNALS__) {
    window.__OPENSHORTS_API_URL__ = import.meta.env.VITE_API_URL || '';
    return;
  }

  const { invoke } = await import('@tauri-apps/api/core');
  window.__OPENSHORTS_API_URL__ = await invoke('backend_url');
}

async function bootstrap() {
  await configureApi();
  createRoot(document.getElementById('root')).render(
    <StrictMode>
      <App />
    </StrictMode>,
  );
}

bootstrap().catch((error) => {
  console.error('Unable to connect OpenShorts to its local backend.', error);
  document.getElementById('root').textContent = 'OpenShorts could not start its local backend.';
});
