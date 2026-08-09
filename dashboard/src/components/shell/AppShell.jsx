import React, { useEffect, useState } from 'react';
import {
  Activity,
  HelpCircle,
  History,
  Image,
  LayoutDashboard,
  Minus,
  PanelLeft,
  Settings,
  Square,
  X,
} from 'lucide-react';
import { openExternal } from '../../lib/openExternal';
import {
  closeWindow,
  isDesktopWindow,
  isWindowMaximized,
  minimizeWindow,
  startWindowDrag,
  toggleMaximizeWindow,
} from '../../lib/windowControls';

const NAV_ITEMS = [
  { id: 'dashboard', label: 'Clip Generator', icon: LayoutDashboard, hint: 'Create and edit clips' },
  { id: 'thumbnails', label: 'YouTube Studio', icon: Image, hint: 'Titles and thumbnails' },
  { id: 'history', label: 'Library', icon: History, hint: 'Local projects' },
  { id: 'settings', label: 'Settings', icon: Settings, hint: 'Providers and privacy' },
];

const TITLES = Object.fromEntries(NAV_ITEMS.map((item) => [item.id, item.label]));

function WindowControls() {
  const [maximized, setMaximized] = useState(false);

  useEffect(() => {
    if (!isDesktopWindow()) return undefined;
    let active = true;
    isWindowMaximized().then((value) => active && setMaximized(value));
    return undefined;
  }, []);

  const toggleMaximize = async () => {
    await toggleMaximizeWindow();
    setMaximized((value) => !value);
  };

  if (!isDesktopWindow()) return null;

  return (
    <div className="window-controls" aria-label="Window controls">
      <button type="button" className="window-control" onClick={minimizeWindow} aria-label="Minimize window" title="Minimize">
        <Minus size={14} strokeWidth={1.8} />
      </button>
      <button type="button" className="window-control" onClick={toggleMaximize} aria-label={maximized ? 'Restore window' : 'Maximize window'} title={maximized ? 'Restore' : 'Maximize'}>
        {maximized ? <PanelLeft size={13} strokeWidth={1.8} /> : <Square size={12} strokeWidth={1.8} />}
      </button>
      <button type="button" className="window-control window-control-danger" onClick={closeWindow} aria-label="Close window" title="Close">
        <X size={14} strokeWidth={1.8} />
      </button>
    </div>
  );
}

export default function AppShell({
  activeTab,
  setActiveTab,
  status,
  keysMissing,
  userProfiles,
  selectedUserId,
  onSelectUser,
  onNewProject,
  onOpenLegal,
  children,
}) {
  const title = TITLES[activeTab] || 'OpenShorts';

  const handleTitlebarPointerDown = async (event) => {
    if (!isDesktopWindow() || event.button !== 0 || event.target.closest('button, a, input, select, textarea')) return;
    await startWindowDrag();
  };

  const handleTitlebarDoubleClick = async (event) => {
    if (!isDesktopWindow() || event.target.closest('button, a, input, select, textarea')) return;
    await toggleMaximizeWindow();
  };

  return (
    <div className="app-shell flex h-screen w-full flex-col overflow-hidden">
      <header
        className="window-titlebar shrink-0"
        data-tauri-drag-region
        onMouseDown={handleTitlebarPointerDown}
        onDoubleClick={handleTitlebarDoubleClick}
      >
        <div className="window-drag-region px-4" data-tauri-drag-region>
          <div className="flex min-w-0 items-center gap-2" data-tauri-drag-region>
            <span className="h-2 w-2 rounded-full bg-brass" aria-hidden="true" />
            <span className="window-title">OpenShorts</span>
            <span className="hidden truncate text-xs text-faint sm:inline">/ {title}</span>
          </div>
        </div>
        <WindowControls />
      </header>

      <div className="flex min-h-0 flex-1">
        <aside className="desktop-rail flex h-full shrink-0 flex-col">
          <div className="flex items-center gap-3 border-b border-rule px-4 py-4 lg:px-5">
            <div className="flex h-8 w-8 shrink-0 items-center justify-center overflow-hidden rounded-control border border-rule bg-paper-3">
              <img src="/logo-openshorts.png" alt="OpenShorts" className="h-full w-full object-cover" />
            </div>
            <div className="hidden min-w-0 lg:block">
              <p className="truncate text-sm font-semibold text-ink">OpenShorts</p>
              <p className="truncate text-xs text-muted">Local video workspace</p>
            </div>
          </div>

          <nav className="flex-1 space-y-1 overflow-y-auto p-3" aria-label="Main navigation">
            {NAV_ITEMS.map((item) => {
              const Icon = item.icon;
              const selected = activeTab === item.id;
              return (
                <button
                  key={item.id}
                  type="button"
                  onClick={() => setActiveTab(item.id)}
                  aria-current={selected ? 'page' : undefined}
                  title={item.hint}
                  className={`group flex w-full items-center gap-3 rounded-control px-3 py-2.5 text-left transition-colors ${selected ? 'bg-paper-3 text-ink' : 'text-muted hover:bg-paper-3/70 hover:text-ink-2'}`}
                >
                  <Icon size={17} strokeWidth={selected ? 2 : 1.7} className={`shrink-0 ${selected ? 'text-brass' : ''}`} />
                  <span className="hidden min-w-0 flex-1 truncate text-sm font-medium lg:block">{item.label}</span>
                  {selected && <span className="hidden h-1.5 w-1.5 shrink-0 rounded-full bg-brass lg:block" aria-hidden="true" />}
                </button>
              );
            })}
          </nav>

          <div className="space-y-1 border-t border-rule p-3">
            <button type="button" onClick={onOpenLegal} className="flex w-full items-center gap-3 rounded-control px-3 py-2 text-left text-xs text-muted transition-colors hover:bg-paper-3 hover:text-ink-2" title="Terms and privacy">
              <HelpCircle size={15} className="shrink-0" />
              <span className="hidden truncate lg:block">Terms & Privacy</span>
            </button>
            <button type="button" onClick={() => openExternal('https://github.com/mutonby/openshorts')} className="flex w-full items-center gap-3 rounded-control px-3 py-2 text-left text-xs text-muted transition-colors hover:bg-paper-3 hover:text-ink-2" title="Open source repository">
              <Activity size={15} className="shrink-0" />
              <span className="hidden truncate lg:block">Open source</span>
            </button>
          </div>
        </aside>

        <main className="desktop-main flex min-h-0 min-w-0 flex-1 flex-col">
          <div className="flex h-14 shrink-0 items-center justify-between border-b border-rule bg-paper px-4 sm:px-6">
            <div className="flex min-w-0 items-center gap-3">
              {status !== 'idle' && activeTab === 'dashboard' && (
                <button type="button" onClick={onNewProject} className="btn-quiet text-xs">
                  New project
                </button>
              )}
              <div className="min-w-0">
                <p className="truncate text-base font-semibold text-ink">{title}</p>
                <p className="hidden truncate text-xs text-muted sm:block">{NAV_ITEMS.find((item) => item.id === activeTab)?.hint}</p>
              </div>
            </div>

            <div className="flex items-center gap-2">
              {userProfiles?.length > 0 && (
                <select value={selectedUserId} onChange={(event) => onSelectUser(event.target.value)} className="hidden max-w-[180px] rounded-control border border-rule-2 bg-paper-2 px-2 py-2 text-xs text-ink-2 outline-none focus:border-brass sm:block" aria-label="Publishing profile">
                  {userProfiles.map((profile) => <option key={profile.username} value={profile.username}>{profile.username}</option>)}
                </select>
              )}
              {keysMissing && (
                <button type="button" onClick={() => setActiveTab('settings')} className="badge-warn" title="Configure your Gemini API key">
                  <span className="hidden sm:inline">Gemini key missing</span>
                  <span className="sm:hidden">Key missing</span>
                </button>
              )}
            </div>
          </div>
          {children}
        </main>
      </div>
    </div>
  );
}
