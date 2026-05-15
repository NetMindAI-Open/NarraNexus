---
code_file: frontend/src/components/artifacts/renderers/ImageRenderer.tsx
last_verified: 2026-05-14
stub: false
---

## 2026-05-14 — drop `version` prop, fetch via `useArtifactRawUrl`

Spec: `reference/self_notebook/specs/2026-05-14-artifact-pointer-model-design.md`

Renderer no longer takes a `version` prop. Uses `useArtifactRawUrl` to get
the token-protected public URL, then `fetchArtifactBlobUrl` (no auth header)
to wrap the bytes as a blob URL for `<img>`.

# ImageRenderer.tsx — Static image renderer for artifact tabs

## Why it exists

Renders `image/png` and `image/jpeg` artifact versions inside the artifact tab pane. No client-side fetch: the browser issues a direct GET to the raw URL (proxied to FastAPI), so HTTP auth cookies apply transparently.

## Upstream / Downstream

- **Used by**: `ArtifactColumn` (next task) via `React.lazy(() => import('./renderers/ImageRenderer'))`, dispatched when `artifact.kind` starts with `image/`.
- **Calls**: `rawUrl()` from `@/types/artifact` to construct `/api/agents/:agentId/artifacts/:artifactId/v:version/raw`.

## Design decisions

**`<img src=rawUrl>` instead of blob URL.** Unlike `AttachmentImage` (which wraps `useAttachmentBlobUrl` to handle JWT-protected attachment downloads), artifact raw URLs are served through the same session-cookie auth as all other API calls. No manual fetch + blob conversion needed; the browser handles it naturally.

**`object-contain` with `bg-[var(--bg-deep)]`.**  Keeps the image within the panel bounds regardless of aspect ratio and uses the deepest background token to create contrast for images with transparent backgrounds.

## Gotchas

No loading / error state. If the image fails to load (404, network error), the browser renders its native broken-image icon. A future iteration could add `onError` handling to display a styled fallback — not worth the complexity for v1.
