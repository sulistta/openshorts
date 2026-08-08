import { StrictMode, useState, useEffect } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.jsx'
import Landing from './Landing.jsx'
import Legal from './Legal.jsx'

async function configureApi() {
  if (!window.__TAURI_INTERNALS__) {
    window.__OPENSHORTS_API_URL__ = import.meta.env.VITE_API_URL || '';
    return;
  }

  const { invoke } = await import('@tauri-apps/api/core');
  window.__OPENSHORTS_API_URL__ = await invoke('backend_url');
}

export function Root() {
  const resolveView = () => {
    const hash = window.location.hash || '';
    if (hash === '#legal') return 'legal';
    // #landing and section anchors keep the landing mounted.
    if (['#landing', '#features', '#how-it-works', '#faq'].includes(hash)) return 'landing';
    if (hash === '#app' || localStorage.getItem('openshorts_skip_landing') === '1') return 'app';
    return 'landing';
  };

  const [view, setView] = useState(resolveView);

  useEffect(() => {
    const handleHashChange = () => setView(resolveView());
    window.addEventListener('hashchange', handleHashChange);
    return () => window.removeEventListener('hashchange', handleHashChange);
  }, []);

  const handleLaunchApp = () => {
    localStorage.setItem('openshorts_skip_landing', '1');
    window.location.hash = '#app';
    setView('app');
  };

  if (view === 'legal') return <Legal />;
  if (view === 'app') return <App />;
  return <Landing onLaunchApp={handleLaunchApp} />;
}

async function bootstrap() {
  await configureApi();
  createRoot(document.getElementById('root')).render(
    <StrictMode>
      <Root />
    </StrictMode>,
  );
}

bootstrap().catch((error) => {
  console.error('Unable to connect OpenShorts to its local backend.', error);
  document.getElementById('root').textContent = 'OpenShorts could not start its local backend.';
});
