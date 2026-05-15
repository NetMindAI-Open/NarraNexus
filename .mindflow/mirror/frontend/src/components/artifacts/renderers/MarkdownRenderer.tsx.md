---
code_file: frontend/src/components/artifacts/renderers/MarkdownRenderer.tsx
last_verified: 2026-05-14
stub: false
---

## 2026-05-14 — drop `version` prop, fetch via `useArtifactRawUrl`

Spec: `reference/self_notebook/specs/2026-05-14-artifact-pointer-model-design.md`

Renderer no longer takes a `version` prop. Uses `useArtifactRawUrl` for the
token-protected public URL; `fetchArtifactText` runs without an Authorization
header.

# MarkdownRenderer.tsx — Markdown artifact renderer

## Why it exists

Fetches a `text/markdown` artifact and renders it with ReactMarkdown + remark-gfm. Artifact Markdown is distinct from chat-bubble Markdown (`ui/Markdown.tsx`) in that the content arrives from a fetch rather than a prop string — hence a separate component.

## Upstream / Downstream

- **Used by**: `ArtifactColumn` via `React.lazy`, dispatched when `artifact.kind === 'text/markdown'`.
- **Calls**: `rawUrl()` from `@/types/artifact` for the fetch target.
- **Bundle**: Both this file and `ui/Markdown.tsx` import from `react-markdown` and `remark-gfm`, which land in the `vendor-markdown` manualChunk. No additional bundle cost once the chunk is loaded.

## Design decisions

**`markdown-content`** (I8, 2026-05-09) — replaced `prose prose-invert` with the app's own `.markdown-content` CSS class from `index.css`. The `prose-invert` Tailwind Typography class hardcoded a dark-theme assumption. The app styles Markdown via `.markdown-content` using CSS custom properties (see `index.css`), so both this renderer and `ui/Markdown.tsx` now use the same theme-aware class. Dropping `prose-invert` means theme changes in the CSS variables flow through automatically without touching the component.

**No rehype-raw.** Unlike `ui/Markdown.tsx` (which enables raw HTML for message bubbles that may contain agent-formatted HTML fragments), the artifact renderer intentionally omits `rehypeRaw` so that literal HTML tags in the Markdown are escaped rather than rendered. An agent that needs rendered HTML should emit a `text/html` artifact rendered by `HtmlRenderer` instead.

**Empty string initial state.** `setText` fires on each `version` change. Starting from `''` means the component renders a blank `<div>` during the fetch rather than stale content from the previous version. Acceptable flicker for the typical small-to-medium Markdown files agents emit.

## Gotchas

**Error handling (2026-05-08-r2).** The fetch chain now checks `r.ok` before calling
`r.text()`. On a non-2xx response it rejects with `Error("HTTP {status}")`, which the
`.catch` handler stores in an `error` state slot. When `error` is set, the component
renders `<div className="p-4 text-red-400">Failed to load: {error}</div>` instead of
the prose container, mirroring the pattern in `CsvRenderer`.

The `useEffect` fetch has no abort controller. If `version` changes quickly (e.g., the user flips through version history), multiple concurrent fetches may race. The last one to resolve wins, which is usually correct (monotonically increasing versions). For a more rigorous fix, add `AbortController` inside the effect — low priority given typical usage patterns.

**Empty body placeholder (M9, 2026-05-09)**: A `!text && !error` guard was added before the prose container render. A 200-OK response with an empty body now shows `"(empty markdown)"` instead of a blank panel, which would look like a load failure to the user.
