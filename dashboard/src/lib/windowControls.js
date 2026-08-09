const isDesktopWindow = () => Boolean(window.__TAURI_INTERNALS__);

let currentWindowPromise;

async function getWindow() {
  if (!isDesktopWindow()) return null;
  if (!currentWindowPromise) {
    currentWindowPromise = import('@tauri-apps/api/window').then(({ getCurrentWindow }) => getCurrentWindow());
  }
  return currentWindowPromise;
}

export async function minimizeWindow() {
  const currentWindow = await getWindow();
  return currentWindow?.minimize();
}

export async function toggleMaximizeWindow() {
  const currentWindow = await getWindow();
  return currentWindow?.toggleMaximize();
}

export async function closeWindow() {
  const currentWindow = await getWindow();
  return currentWindow?.close();
}

export async function isWindowMaximized() {
  const currentWindow = await getWindow();
  return currentWindow ? currentWindow.isMaximized() : false;
}

export async function startWindowDrag() {
  const currentWindow = await getWindow();
  return currentWindow?.startDragging();
}

export { isDesktopWindow };
