// Tauri resolves the loopback backend before React mounts. Keeping the lookup
// lazy also preserves the normal Vite proxy flow for browser-only development.
const getApiBaseUrl = () => window.__OPENSHORTS_API_URL__ || import.meta.env.VITE_API_URL || '';

export const getApiUrl = (path) => {
    if (path.startsWith('http')) return path;
    // Ensure path starts with / if not present
    const normalizedPath = path.startsWith('/') ? path : `/${path}`;
    return `${getApiBaseUrl()}${normalizedPath}`;
};
