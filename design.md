# OpenShorts desktop design system

OpenShorts is a local desktop video workspace. The interface is a focused workbench, not a marketing website. The system is dark-only, quiet, and information-dense without feeling cramped.

## Product surface

The single Tauri window opens directly to the workspace. Primary areas are:

- **Clip Generator** — choose a local file or video URL, process it, review generated clips, edit, and download them.
- **Thumbnail Studio** — create titles, thumbnails, and descriptions for local use.
- **Library** — reopen durable local projects, preview clips, download, and explicitly delete.
- **Settings** — manage BYOK providers, privacy, storage, and legal text.

There is no Home/Landing page, SEO route, marketing hero, feature grid, FAQ route, or hash-based navigation.

## Visual language

- Dark fixed appearance with neutral charcoal surfaces.
- System UI typography: `ui-sans-serif`, `-apple-system`, `BlinkMacSystemFont`, and `Segoe UI` fallbacks.
- One restrained warm accent derived from the OpenShorts icon; semantic green, amber, and red are reserved for state.
- Hairline separators, flat surfaces, restrained shadows, and clear selected states.
- No gradients, glow effects, blueprint grids, glassmorphism, fake browser chrome, marketing counters, or decorative HUDs.
- Sentence-case interface copy. Preserve user-generated titles, captions, transcripts, keys, filenames, and logs exactly.
- Native controls use compact rectangular radii; pills are reserved for status badges.

## Window chrome

The desktop build uses a custom unified titlebar with a real drag region and accessible minimize, maximize/restore, and close controls. Tauri owns window actions; browser/Vite mode hides those controls. The titlebar must not imitate Safari or draw fake traffic-light chrome.

The Tauri window remains resizable with a minimum of 1024×720. Keep the local sidecar lifecycle and loopback URL bootstrap unchanged.

## Tokens

The canonical values live in `dashboard/src/tokens.css`. The semantic groups are:

- surfaces: `paper`, `paper-2`, `paper-3`, `paper-4`
- content: `ink`, `ink-2`, `muted`, `faint`
- interaction: `accent`, `focus`, `ok`, `warn`, `danger`
- geometry: `radius-card`, `radius-panel`, `radius-input`, `radius-control`
- motion: `ease-out`, `ease-in-out`, `dur-fast`, `dur-short`, `dur-panel`

Tailwind aliases remain temporarily for feature components while they migrate. New code must use semantic tokens and shared primitives.

## Interaction rules

- Every interactive control has visible default, hover, focus-visible, active, disabled, loading, error, and success states where applicable.
- Keyboard focus is never hidden or animated.
- Sheets trap focus, close on Escape, close on backdrop click when safe, and restore focus to the opener.
- Deletions require an explicit confirmation before the existing `confirm=true` API request.
- Success is quiet and inline; avoid celebratory banners and star prompts.
- Respect `prefers-reduced-motion`.

## Functional boundaries

Do not change these as part of a visual redesign:

- Tauri → local backend URL bootstrap and `127.0.0.1` loopback binding.
- `getApiUrl`, `apiFetch`, and `apiJson` behavior.
- Existing API paths and BYOK request headers, except when a provider migration explicitly changes them.
- `gemini_key`, `elevenLabsKey_v1`, and `openshorts_session` storage formats.
- Durable project manifests, restore, local library URLs, and explicit deletion.
- Remotion browser rendering and the server edit chain.

Provider, GitHub, and documentation links should use `openExternal` in the desktop build. Media and loopback URLs remain in-app.
