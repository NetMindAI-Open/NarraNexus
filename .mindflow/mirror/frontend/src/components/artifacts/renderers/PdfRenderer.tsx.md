---
code_file: frontend/src/components/artifacts/renderers/PdfRenderer.tsx
last_verified: 2026-05-27
stub: false
---

## 2026-05-27 — same Dismiss-loop fix as HtmlRenderer

`heal.attempt` now via `attemptRef`; load effect deps reduced to
`[url]`. See HtmlRenderer.tsx.md + useArtifactHeal.ts.md for the
shared bug story (modal stuck open after Dismiss, P0 2026-05-25).

## 2026-05-14 — drop `version` prop, fetch via `useArtifactRawUrl`

Spec: `reference/self_notebook/specs/2026-05-14-artifact-pointer-model-design.md`

Renderer no longer takes a `version` prop. Uses `useArtifactRawUrl` for the
token-protected public URL, then `fetchArtifactBlobUrl` to hand a blob URL to
`<object>`. The reason `<object>`+blob is still used (instead of a direct URL
in `data=`) is the long-standing Firefox PDF.js same-origin-XHR requirement —
unchanged from the pre-pointer-model design.

# PdfRenderer.tsx — Native PDF renderer using `<object>`

## Why it exists

PDF rendering requires browser-native plugin infrastructure that differs across engines:
- **Chromium**: PDFium, built-in; largely ignores the iframe `sandbox` attribute for plugin content, making the security guarantee illusory.
- **Firefox**: PDF.js; requires same-origin XHR to load its own worker scripts. The iframe `sandbox="allow-scripts"` (without `allow-same-origin`) denies same-origin, so PDF.js fails silently.
- **WKWebView** (macOS/iOS): uses Apple's Preview framework; behaves differently from both.

`HtmlRenderer` was previously re-used for PDF by relying on the browser's native PDF viewer inside an iframe. That approach is inconsistent and the `sandbox` attribute provides false safety for Chromium's PDFium while actively breaking Firefox PDF.js.

`PdfRenderer` replaces this with `<object data type="application/pdf">`, the W3C-standard mechanism for embedding plugin content. The browser picks its native renderer — no sandbox attribute manipulation needed. The response CSP (`default-src 'none'; object-src 'self'`) on the `/raw` endpoint still limits what embedded PDF actions can do.

## Upstream / Downstream

- **Used by**: `ArtifactColumn` via `React.lazy`, dispatched when `artifact.kind === 'application/pdf'`.
- **Calls**: `rawUrl()` from `@/types/artifact` to build the `<object data=>` URL.
- **Fallback**: if `<object>` is unsupported or the PDF cannot be embedded, a plain link renders inside the `<object>`'s content slot.

## Design decisions

**`<object>` over `<embed>`**: Both work for PDF, but `<object>` has a built-in fallback content slot (children) that renders when the plugin cannot load. `<embed>` has no fallback mechanism.

**No iframe**: The security rationale for `sandbox="allow-scripts"` without `allow-same-origin` does not apply cleanly to PDF content (PDF is not HTML; its "scripts" are PDF actions, not JS). Using `<object>` avoids the mis-applied sandbox entirely.

**`aria-label`**: `<object>` has no inherent accessible name. The `aria-label={artifact.title}` gives screen readers a meaningful label for the embedded content region.

## Gotchas

The `<object>` element's fallback content is rendered by the browser only if the plugin fails to load. It is invisible during normal PDF display. Style the fallback with muted opacity so it does not look like an error if briefly visible during load.
