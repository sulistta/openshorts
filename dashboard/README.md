# OpenShorts desktop frontend

This directory contains the React/Vite interface and the Tauri v2 desktop
shell. The Python video pipeline is launched as a local sidecar in packaged
builds.

## Commands

```bash
npm install
npm run setup:backend    # creates ../.venv and installs Python requirements
npm run tauri:dev      # native window + local development backend
npm run tauri:build    # native installer and packaged Python sidecar
npm run dev            # browser-only UI development
npm run build          # frontend production build
```

`tauri:dev` runs `scripts/tauri-dev.mjs`, which starts
`desktop/backend.py` at `127.0.0.1:37831` and Vite at `127.0.0.1:1420`.
It automatically uses the project `.venv`; create it first with
`npm run setup:backend`.

`tauri:build` first installs the selected PyTorch wheel (CPU by default), then
runs `desktop/build_sidecar.py`. It uses PyInstaller to
write a platform-specific executable to `src-tauri/binaries/`; the release
Tauri configuration then includes it. That generated binary is intentionally
ignored by Git and must be produced on the target platform.

Set `OPENSHORTS_TORCH_INDEX` to a matching PyTorch CUDA or ROCm wheel index
before setup/build when GPU acceleration is required.

The production app asks Rust for the local backend URL before React mounts, so
the UI never depends on a remote API hostname. Browser-only development uses
the Vite proxy configured in `vite.config.js`.
