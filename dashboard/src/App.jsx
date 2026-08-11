import React, { useState, useEffect, useRef } from 'react';
import { ChevronDown, Check, Activity, X, Terminal, Shield, Globe, RotateCcw, AlertTriangle, KeyRound, Loader2, Download } from 'lucide-react';
import KeyInput from './components/KeyInput';
import MediaInput from './components/MediaInput';
import ResultCard from './components/ResultCard';
import ProcessingAnimation from './components/ProcessingAnimation';
import ThumbnailStudio from './components/ThumbnailStudio';
import HistoryTab from './components/HistoryTab';
import Modal from './components/ui/Modal';
import AppShell from './components/shell/AppShell';
import ClipWorkspace from './components/workspace/ClipWorkspace';
import SettingsWorkspace from './components/workspace/SettingsWorkspace';
import LegalSheet from './components/LegalSheet';
import { apiFetch, apiJson } from './lib/api';
import { getApiUrl } from './config';
import { openExternal } from './lib/openExternal';
import { saveBlob } from './lib/download';

// Legacy-compatible local key encoding. This is not a security boundary.
const SECRET_KEY = import.meta.env.VITE_ENCRYPTION_KEY || "OpenShorts-Static-Salt-Change-Me";
const ENCRYPTION_PREFIX = "ENC:";

const encrypt = (text) => {
  if (!text) return '';
  try {
    const xor = text.split('').map((c, i) =>
      String.fromCharCode(c.charCodeAt(0) ^ SECRET_KEY.charCodeAt(i % SECRET_KEY.length))
    ).join('');
    return ENCRYPTION_PREFIX + btoa(xor);
  } catch (e) {
    console.error("Encryption failed", e);
    return text;
  }
};

const decrypt = (text) => {
  if (!text) return '';
  if (text.startsWith(ENCRYPTION_PREFIX)) {
    try {
      const raw = text.slice(ENCRYPTION_PREFIX.length);
      // Check if it's plain base64 or our custom XOR (simple try)
      const xor = atob(raw);
      const result = xor.split('').map((c, i) =>
        String.fromCharCode(c.charCodeAt(0) ^ SECRET_KEY.charCodeAt(i % SECRET_KEY.length))
      ).join('');
      return result;
    } catch (e) {
      // Fallback if decryption fails (might be old plain text)
      return '';
    }
  }
  // Backward compatibility: if no prefix, keep the old plain-text value so it
  // can be re-saved in the current local format.
  // For migration: Return text as is, so it populates the field, and next save will encrypt it.
  return text;
};

const SESSION_KEY = 'openshorts_session';
const SESSION_MAX_AGE = 3600000; // 1 hour (matches server job retention)
// Mock polling function
const pollJob = async (jobId) => {
  const res = await apiFetch(`/api/status/${jobId}`);
  if (!res.ok) throw new Error('Status check failed');
  return res.json();
};

function App() {
  // Durable local-library URLs (per clip index) for the current job.
  const [durableClips, setDurableClips] = useState({});

  const [apiKey, setApiKey] = useState(localStorage.getItem('gemini_key') || '');
  // ElevenLabs API State - Load encrypted
  const [elevenLabsKey, setElevenLabsKey] = useState(() => {
    const stored = localStorage.getItem('elevenLabsKey_v1');
    if (stored) return decrypt(stored);
    return '';
  });

  const [showKeyModal, setShowKeyModal] = useState(false);
  const [jobId, setJobId] = useState(null);
  const [status, setStatus] = useState('idle'); // idle, processing, complete, error
  const [results, setResults] = useState(null);
  // Bulk subtitles: apply one style to every clip of the job (triggered from
  // within a clip's subtitle modal via "apply to all").
  const [bulkSub, setBulkSub] = useState({ running: false, current: 0, total: 0, errors: 0 });
  const [downloadingAll, setDownloadingAll] = useState(false);
  // Pre-flight quality gate: { info: {max_height, min_height, cookies_invalid}, data }
  const [qualityGate, setQualityGate] = useState(null);
  const [logs, setLogs] = useState([]);
  const [logsVisible, setLogsVisible] = useState(true);
  const [processingMedia, setProcessingMedia] = useState(null);
  const [activeTab, setActiveTab] = useState('dashboard'); // dashboard, settings
  // Reopened-project state: per-clip {index, server_file, active_layers}
  // restored from the backend so ResultCards resume editing where they left off.
  const [projectState, setProjectState] = useState(null);
  // True when the current job was reopened from the library: its source video
  // was never persisted, so the session must not fall back to /api/source.
  const [noSource, setNoSource] = useState(false);

  const [sessionRecovered, setSessionRecovered] = useState(false);
  const [showLegal, setShowLegal] = useState(false);

  // Silent-success "saved" states for the settings key inputs (design.md: no alert popups)
  const [elevenLabsSaved, setElevenLabsSaved] = useState(false);

  // Sync state for original video playback
  const [syncedTime, setSyncedTime] = useState(0);
  const [isSyncedPlaying, setIsSyncedPlaying] = useState(false);
  const [syncTrigger, setSyncTrigger] = useState(0);

  const handleClipPlay = (startTime) => {
    setSyncedTime(startTime);
    setIsSyncedPlaying(true);
    setSyncTrigger(prev => prev + 1);
  };

  const handleClipPause = () => {
    setIsSyncedPlaying(false);
  };

  // --- Durable project persistence ---
  // Debounced sync of each clip's browser-only edit state (Remotion layers +
  // current server file) to the backend, so a reopened project resumes intact.
  const clipStateSync = useRef({ jobId: null, pending: {}, timer: null });

  const flushClipState = () => {
    const s = clipStateSync.current;
    if (s.timer) { clearTimeout(s.timer); s.timer = null; }
    const entries = Object.entries(s.pending);
    if (!s.jobId || entries.length === 0) return;
    const clips = entries.map(([i, v]) => ({
      index: Number(i),
      active_layers: v.activeLayers,
      server_file: v.serverVideoFile,
    }));
    s.pending = {};
    apiFetch(`/api/projects/${s.jobId}/state`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ clips }),
    }).catch(() => {});
  };

  const handleClipStateChange = (index, state) => {
    if (!jobId) return;
    const s = clipStateSync.current;
    if (s.jobId !== jobId) { s.pending = {}; s.jobId = jobId; }
    s.pending[index] = state;
    if (s.timer) clearTimeout(s.timer);
    s.timer = setTimeout(flushClipState, 2000);
  };

  // Reopen a durable project from the local library.
  const restoreProject = async (projectJobId) => {
    const data = await apiJson(`/api/projects/${projectJobId}/restore`, { method: 'POST' });
    flushClipState();
    setProjectState(data.project_state || null);
    setNoSource(true);
    setJobId(data.job_id);
    setResults(data.result || null);
    setLogs(['♻️ Project restored from your library.']);
    setProcessingMedia(null);
    setQualityGate(null);
    setStatus('complete');
    setActiveTab('dashboard');
  };

  // Apply one subtitle style to every clip of the job, sequentially.
  const handleBulkSubtitles = async (options) => {
    const clips = results?.clips || [];
    const total = clips.length;
    if (!total) return;
    setBulkSub({ running: true, current: 0, total, errors: 0 });
    let errors = 0;
    for (let i = 0; i < total; i++) {
      setBulkSub({ running: true, current: i + 1, total, errors });
      try {
        const res = await apiFetch('/api/subtitle', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            job_id: jobId,
            clip_index: i,
            position: options.position,
            font_size: options.fontSize,
            font_name: options.fontName,
            font_color: options.fontColor,
            border_color: options.borderColor,
            border_width: options.borderWidth,
            bg_color: options.bgColor,
            bg_opacity: options.bgOpacity,
            style: options.style || 'classic',
            highlight_color: options.highlightColor || '#FFD700',
            effect: options.effect || 'none',
            base_opacity: options.baseOpacity ?? 1.0,
            uppercase: options.uppercase || false,
            // Chain from the clip's current server file (its video_url basename).
            input_filename: (clips[i].video_url || '').split('/').pop(),
          }),
        });
        if (!res.ok) errors++;
      } catch {
        errors++;
      }
    }
    setBulkSub({ running: false, current: total, total, errors });
    // Refresh results so each ResultCard picks up its new subtitled video_url.
    try {
      const data = await pollJob(jobId);
      if (data.result) setResults(data.result);
    } catch { /* keep current results */ }
  };

  const handleDownloadAll = async () => {
    if (!jobId) return;
    setDownloadingAll(true);
    try {
      const res = await apiFetch(`/api/jobs/${jobId}/download-all`);
      if (!res.ok) throw new Error(await res.text());
      const blob = await res.blob();
      await saveBlob(blob, {
        filename: `openshorts_clips_${(jobId || '').slice(0, 8)}.zip`,
        filters: [{ name: 'ZIP archive', extensions: ['zip'] }],
      });
    } catch (e) {
      alert(`Download failed: ${e.message}`);
    } finally {
      setDownloadingAll(false);
    }
  };

  // Session Recovery: Restore on mount
  useEffect(() => {
    try {
      const saved = localStorage.getItem(SESSION_KEY);
      if (!saved) return;
      const session = JSON.parse(saved);
      if (Date.now() - session.timestamp > SESSION_MAX_AGE) {
        localStorage.removeItem(SESSION_KEY);
        return;
      }
      if (session.jobId && session.status && session.status !== 'idle') {
        setJobId(session.jobId);
        setResults(session.results || null);
        // Restore the source preview. Older sessions (or uploads) saved no
        // media, so fall back to the backend-served source for this job —
        // except for reopened projects, whose source was never persisted.
        if (session.processingMedia) setProcessingMedia(session.processingMedia);
        else if (!session.noSource) setProcessingMedia({ type: 'server', payload: `/api/source/${session.jobId}` });
        if (session.noSource) setNoSource(true);
        if (session.projectState) setProjectState(session.projectState);
        if (session.activeTab) setActiveTab(session.activeTab);
        // If was processing, resume polling; if complete/error, just show results
        setStatus(session.status === 'processing' ? 'processing' : session.status);
        setSessionRecovered(true);
        setTimeout(() => setSessionRecovered(false), 5000);
      }
    } catch (e) {
      localStorage.removeItem(SESSION_KEY);
    }
  }, []);

  // Session Recovery: Save state changes
  useEffect(() => {
    if (status === 'idle') {
      localStorage.removeItem(SESSION_KEY);
      return;
    }
    try {
      // URL (YouTube) media serializes as-is. Uploaded 'file' media is a blob
      // that can't be persisted, so point the recovered preview at the source
      // served by the backend instead of dropping it.
      let persistMedia = null;
      if (processingMedia?.type === 'url') persistMedia = processingMedia;
      else if (processingMedia && jobId) persistMedia = { type: 'server', payload: `/api/source/${jobId}` };
      const sessionData = {
        jobId,
        status,
        results,
        processingMedia: persistMedia,
        activeTab,
        noSource,
        projectState,
        timestamp: Date.now()
      };
      localStorage.setItem(SESSION_KEY, JSON.stringify(sessionData));
    } catch (e) {
      // localStorage full or serialization error - ignore
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobId, status, results, activeTab, noSource, projectState]);

  useEffect(() => {
    if (apiKey) localStorage.setItem('gemini_key', apiKey);
  }, [apiKey]);

  useEffect(() => {
    localStorage.removeItem('postizApiKey_v1');
    localStorage.removeItem('postizBaseUrl_v1');
    localStorage.removeItem('postizIntegrationIds_v1');
  }, []);

  useEffect(() => {
    if (elevenLabsKey) {
      localStorage.setItem('elevenLabsKey_v1', encrypt(elevenLabsKey));
    }
  }, [elevenLabsKey]);

  // Fetch durable local-library URLs for the current job.
  useEffect(() => {
    if (!jobId || !(results?.clips?.length)) { setDurableClips({}); return; }
    let cancelled = false;
    apiJson('/api/history')
      .then((d) => {
        if (cancelled) return;
        const map = {};
        for (const v of (d.videos || [])) {
          if (v.job_id === jobId && v.clip_index != null) map[v.clip_index] = getApiUrl(v.view_url);
        }
        setDurableClips(map);
      })
      .catch(() => {});
    return () => { cancelled = true; };
  }, [jobId, results]);

  useEffect(() => {
    let interval;
    if ((status === 'processing' || status === 'completed') && jobId) {
      interval = setInterval(async () => {
        try {
          const data = await pollJob(jobId);
          console.log("Job status:", data);

          // Update results if available (real-time)
          if (data.result) {
            setResults(data.result);
          }

          if (data.status === 'completed') {
            setStatus('complete');
            clearInterval(interval);
          } else if (data.status === 'failed') {
            setStatus('error');
            const errorMsg = data.error || (data.logs && data.logs.length > 0 ? data.logs[data.logs.length - 1] : "Process failed");
            setLogs(prev => [...prev, "Error: " + errorMsg]);
            clearInterval(interval);
          } else {
            // Update logs if available
            if (data.logs) setLogs(data.logs);
          }
        } catch (e) {
          console.error("Polling error", e);
        }
      }, 2000);
    }
    return () => clearInterval(interval);
  }, [status, jobId]);

  const keysMissing = !apiKey;

  const handleProcess = async (data, forceLowQuality = false) => {
    if (keysMissing) {
      setShowKeyModal(true);
      return;
    }
    setStatus('processing');
    setLogs(["Starting process..."]);
    setResults(null);
    setProcessingMedia(data);
    setQualityGate(null);
    setProjectState(null);
    setNoSource(false);

    try {
      let body;
      const headers = apiKey ? { 'X-Gemini-Key': apiKey } : {};

      if (data.type === 'url') {
        headers['Content-Type'] = 'application/json';
        body = JSON.stringify({
          url: data.payload,
          acknowledged: !!data.acknowledged,
          output_format: data.outputFormat || 'auto',
          force_low_quality: forceLowQuality,
        });
      } else {
        const formData = new FormData();
        formData.append('file', data.payload);
        formData.append('acknowledged', data.acknowledged ? 'true' : 'false');
        formData.append('output_format', data.outputFormat || 'auto');
        body = formData;
      }

      const res = await apiFetch('/api/process', { method: 'POST', headers, body });

      if (!res.ok) throw new Error(await res.text());
      const resData = await res.json();

      // Quality gate: the source is below the minimum resolution. On confirm we
      // resend with force_low_quality.
      if (resData.needs_confirmation) {
        setStatus('idle');
        setQualityGate({ info: resData.quality_check, data });
        return;
      }

      setJobId(resData.job_id);

    } catch (e) {
      setStatus('error');
      setLogs(l => [...l, `Error starting job: ${e.message}`]);
    }
  };

  const handleReset = () => {
    // Flush any pending edit-state sync before leaving the project.
    flushClipState();
    setStatus('idle');
    setJobId(null);
    setResults(null);
    setLogs([]);
    setProcessingMedia(null);
    setProjectState(null);
    setNoSource(false);
    localStorage.removeItem(SESSION_KEY);
  };

  return (
    <AppShell
      activeTab={activeTab}
      setActiveTab={setActiveTab}
      status={status}
      keysMissing={keysMissing}
      onNewProject={handleReset}
      onOpenLegal={() => setShowLegal(true)}
    >
      {keysMissing && activeTab !== 'settings' && (
        <div className="mx-4 mt-4 flex shrink-0 flex-wrap items-center justify-between gap-3 rounded-input border border-rule bg-paper-2 px-4 py-3 sm:mx-6">
          <div className="flex items-center gap-3 text-sm text-ink-2">
            <KeyRound size={16} className="shrink-0 text-warn" />
            <span><strong className="text-ink">Gemini key required.</strong> Add it in Settings to process a video.</span>
          </div>
          <button type="button" onClick={() => setActiveTab('settings')} className="btn-quiet text-xs">Open Settings</button>
        </div>
      )}

      {sessionRecovered && (
        <div className="mx-4 mt-3 flex shrink-0 items-center justify-between rounded-input border border-rule bg-paper-2 px-4 py-3 text-sm sm:mx-6">
          <div className="flex items-center gap-2 text-ink-2"><RotateCcw size={15} className="text-brass" /><span>Previous session restored.</span></div>
          <button type="button" onClick={() => setSessionRecovered(false)} className="text-muted hover:text-ink" aria-label="Dismiss session notice"><X size={14} /></button>
        </div>
      )}

      {activeTab === 'settings' && (
        <SettingsWorkspace>
          <div className="mb-8 flex flex-wrap items-start justify-between gap-4">
            <div>
              <p className="eyebrow mb-2">Settings</p>
              <h1 className="text-2xl font-semibold text-ink">Providers and privacy</h1>
              <p className="mt-2 max-w-xl text-sm leading-relaxed text-muted">Keys stay in this local app and are sent only with the requests that need them.</p>
            </div>
            <div className="flex items-center gap-2 text-xs text-ok"><Shield size={14} /> Local-only storage</div>
          </div>

          <div className="space-y-4">
            <KeyInput onKeySet={setApiKey} savedKey={apiKey} />

            <section className="card p-5 sm:p-6">
              <div className="mb-5 flex items-start justify-between gap-4">
                <div>
                  <p className="eyebrow mb-2">Translation</p>
                  <h2 className="text-base font-semibold text-ink">ElevenLabs</h2>
                  <p className="mt-1 text-sm leading-relaxed text-muted">Bring your own key to translate and dub clips.</p>
                </div>
                <Globe size={18} className="text-brass" />
              </div>
              <div className="flex flex-col gap-2 sm:flex-row">
                <input type="password" value={elevenLabsKey} onChange={(e) => setElevenLabsKey(e.target.value)} className="input-field" placeholder="ElevenLabs API key" />
                <button type="button" onClick={() => { if (elevenLabsKey) { localStorage.setItem('elevenLabsKey_v1', encrypt(elevenLabsKey)); setElevenLabsSaved(true); setTimeout(() => setElevenLabsSaved(false), 2000); } }} className={elevenLabsSaved ? 'badge-ok px-4' : 'btn-quiet shrink-0'}>
                  {elevenLabsSaved ? <><Check size={12} /> Saved</> : 'Save key'}
                </button>
              </div>
              <button type="button" onClick={() => openExternal('https://elevenlabs.io/app/settings/api-keys')} className="mt-4 text-xs text-brass underline underline-offset-2">Open ElevenLabs API keys</button>
            </section>

            <section className="card p-5 sm:p-6">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div>
                  <p className="eyebrow mb-2">About</p>
                  <h2 className="text-base font-semibold text-ink">OpenShorts desktop</h2>
                  <p className="mt-1 text-sm leading-relaxed text-muted">Local projects, local processing, and no account required.</p>
                </div>
                <div className="flex gap-2">
                  <button type="button" onClick={() => setShowLegal(true)} className="btn-ghost text-xs">Terms & Privacy</button>
                  <button type="button" onClick={() => openExternal('https://github.com/mutonby/openshorts')} className="btn-quiet text-xs">Source</button>
                </div>
              </div>
            </section>
          </div>
        </SettingsWorkspace>
      )}

      {activeTab === 'history' && (
        <ClipWorkspace state="library"><HistoryTab onReopenProject={restoreProject} /></ClipWorkspace>
      )}

      {activeTab === 'thumbnails' && (
        <ClipWorkspace state="studio"><ThumbnailStudio geminiApiKey={apiKey} /></ClipWorkspace>
      )}

      {activeTab === 'dashboard' && status === 'idle' && (
        <ClipWorkspace state="idle">
          <div className="mx-auto flex min-h-full w-full max-w-3xl items-center justify-center p-4 sm:p-8">
            <div className="w-full">
              <div className="mb-8 max-w-xl">
                <p className="eyebrow mb-3">Create clips</p>
                <h1 className="text-3xl font-semibold tracking-tight text-ink sm:text-4xl">Turn long videos into a focused short-form set.</h1>
                <p className="mt-3 text-base leading-relaxed text-muted">Choose a source, set the output format, and let the local pipeline find the moments worth keeping.</p>
              </div>
              <MediaInput onProcess={handleProcess} onOpenLegal={() => setShowLegal(true)} isProcessing={status === 'processing'} />
            </div>
          </div>
        </ClipWorkspace>
      )}

      {activeTab === 'dashboard' && (status === 'processing' || status === 'complete' || status === 'error') && (
        <ClipWorkspace state={status}>
          <div className="flex h-full min-h-0 flex-col gap-4 p-4 md:flex-row sm:p-6">
            <div className={`${status === 'complete' ? 'md:w-[30%]' : 'md:w-[55%]'} flex min-h-0 w-full shrink-0 flex-col gap-4 overflow-y-auto custom-scrollbar rounded-panel border border-rule bg-paper-2 p-4 sm:p-5`}>
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2 text-sm font-semibold text-ink"><Activity size={17} className={status === 'processing' ? 'animate-pulse text-brass' : 'text-brass'} /> Analysis</div>
                <span className={status === 'processing' ? 'badge-brass' : status === 'complete' ? 'badge-ok' : 'badge-danger'}>{status}</span>
              </div>
              {processingMedia && <ProcessingAnimation media={processingMedia} isComplete={status === 'complete'} syncedTime={syncedTime} isSyncedPlaying={isSyncedPlaying} syncTrigger={syncTrigger} />}
              <div className="flex min-h-[160px] flex-1 flex-col overflow-hidden rounded-input border border-rule bg-paper">
                <div className="flex items-center justify-between border-b border-rule px-3 py-2"><span className="readout flex items-center gap-2"><Terminal size={12} /> Diagnostics</span><button type="button" onClick={() => setLogsVisible(!logsVisible)} className="text-muted hover:text-ink" aria-label="Toggle diagnostics">{logsVisible ? <ChevronDown size={14} /> : <ChevronDown size={14} className="-rotate-90" />}</button></div>
                {logsVisible && <div className="flex-1 space-y-1 overflow-y-auto p-3 font-mono text-xs text-muted custom-scrollbar">{logs.map((log, i) => <div key={i} className={log.toLowerCase().includes('error') ? 'text-danger' : ''}>{log}</div>)}{status === 'processing' && <div className="animate-pulse text-brass">Working…</div>}</div>}
              </div>
            </div>

            <div className={`${status === 'complete' ? 'md:w-[70%]' : 'md:w-[45%]'} flex min-h-0 w-full min-w-0 flex-1 flex-col overflow-hidden rounded-panel border border-rule bg-paper-2 p-4 sm:p-5`}>
              <div className="mb-4 flex flex-wrap items-center gap-2">
                <h2 className="text-base font-semibold text-ink">Generated clips</h2>
                {results?.clips?.length > 0 && <span className="readout rounded-full bg-paper-3 px-2.5 py-1">{results.clips.length} clips</span>}
                {results?.cost_analysis && <span className="readout rounded-full bg-paper-3 px-2.5 py-1">Gemini · ${results.cost_analysis.total_cost.toFixed(5)}</span>}
                {results?.clips?.length > 0 && status === 'complete' && <div className="ml-auto flex gap-2"><button type="button" onClick={handleDownloadAll} disabled={downloadingAll} className="btn-ghost text-xs">{downloadingAll ? 'Zipping…' : <><Download size={14} /> Download all</>}</button></div>}
              </div>
              <div className="min-h-0 flex-1 overflow-y-auto p-1 custom-scrollbar">
                {results?.clips?.length > 0 ? <div className={`grid gap-4 pb-6 ${status === 'complete' ? 'xl:grid-cols-2' : 'grid-cols-1'}`}>{results.clips.map((clip, i) => <ResultCard key={`${jobId}-${i}`} clip={clip} index={i} jobId={jobId} initialState={projectState?.clips?.find((c) => c.index === i) || null} onStateChange={handleClipStateChange} durableUrl={durableClips[i]} geminiApiKey={apiKey} elevenLabsKey={elevenLabsKey} onPlay={handleClipPlay} onPause={handleClipPause} onBulkSubtitle={handleBulkSubtitles} clipCount={results.clips.length} bulkProgress={bulkSub} />)}</div> : <div className="flex h-full min-h-[240px] flex-col items-center justify-center gap-3 text-muted">{status === 'processing' ? <Loader2 size={28} className="animate-spin text-brass" /> : <AlertTriangle size={24} className="text-danger" />}<p className="text-sm">{status === 'processing' ? 'Waiting for clips…' : 'Generation failed.'}</p></div>}
              </div>
            </div>
          </div>
        </ClipWorkspace>
      )}

      <Modal isOpen={showKeyModal} onClose={() => setShowKeyModal(false)} eyebrow="Setup" title="Gemini API key required" footer={<div className="flex gap-2"><button type="button" onClick={() => setShowKeyModal(false)} className="btn-ghost flex-1">Cancel</button><button type="button" onClick={() => { setShowKeyModal(false); setActiveTab('settings'); }} className="btn-primary flex-1">Open Settings</button></div>}>
        <p className="text-sm leading-relaxed text-ink-2">OpenShorts needs a Gemini key for AI processing. Add it in Settings to continue.</p>
      </Modal>

      {qualityGate && <Modal isOpen onClose={() => setQualityGate(null)} size="md" eyebrow="Source quality" title="Process lower-quality video?"><div className="space-y-4"><p className="text-sm leading-relaxed text-ink-2">YouTube offers <strong className="text-brass">{qualityGate.info.max_height}p</strong> for this source, below the recommended {qualityGate.info.min_height}p.</p>{qualityGate.info.cookies_invalid && <p className="text-xs leading-relaxed text-muted">Your YouTube cookies may be expired. Refreshing them can unlock HD.</p>}<div className="flex justify-end gap-2"><button type="button" onClick={() => setQualityGate(null)} className="btn-ghost">Cancel</button><button type="button" onClick={() => { const d = qualityGate.data; setQualityGate(null); handleProcess(d, true); }} className="btn-primary">Process anyway</button></div></div></Modal>}
      <LegalSheet isOpen={showLegal} onClose={() => setShowLegal(false)} />
    </AppShell>
  );
}

export default App;
