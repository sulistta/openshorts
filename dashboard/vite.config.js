import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Browser-only development proxies to the same loopback backend that Tauri
// starts in desktop development. Tauri itself resolves this URL through Rust.
const backend = process.env.VITE_PROXY_TARGET || 'http://127.0.0.1:37831'

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    host: '127.0.0.1',
    port: 1420,
    strictPort: true,
    proxy: {
      '/api': { target: backend, changeOrigin: true },
      '/videos': { target: backend, changeOrigin: true },
      '/thumbnails': { target: backend, changeOrigin: true },
    }
  }
})
