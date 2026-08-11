function isTauriDesktop() {
  return typeof window !== 'undefined' && Boolean(window.__TAURI_INTERNALS__);
}

function saveInBrowser(blob, filename) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  link.style.display = 'none';
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 0);
}

/**
 * Save binary content using the browser download flow or, in the packaged
 * desktop app, a native save dialog followed by Tauri's scoped filesystem API.
 * Returns false when the user cancels the native dialog.
 */
export async function saveBlob(blob, { filename, filters = [] }) {
  if (!isTauriDesktop()) {
    saveInBrowser(blob, filename);
    return true;
  }

  const [{ save }, { writeFile }] = await Promise.all([
    import('@tauri-apps/plugin-dialog'),
    import('@tauri-apps/plugin-fs'),
  ]);
  const path = await save({ defaultPath: filename, filters });
  if (!path) return false;

  await writeFile(path, new Uint8Array(await blob.arrayBuffer()));
  return true;
}

export async function downloadUrl(url, options) {
  const response = await fetch(url);
  if (!response.ok) {
    throw new Error(`Download failed (${response.status})`);
  }
  return saveBlob(await response.blob(), options);
}
