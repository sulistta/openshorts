export async function openExternal(url) {
  if (window.__TAURI_INTERNALS__) {
    const { openUrl } = await import('@tauri-apps/plugin-opener');
    return openUrl(url);
  }

  window.open(url, '_blank', 'noopener,noreferrer');
}
