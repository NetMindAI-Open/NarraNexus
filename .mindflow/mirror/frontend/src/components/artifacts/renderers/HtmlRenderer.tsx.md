---
code_file: frontend/src/components/artifacts/renderers/HtmlRenderer.tsx
last_verified: 2026-05-27
stub: false
---

## 2026-05-27 — break the Dismiss-modal loop (P0 fix)

The HEAD-probe useEffect previously depended on `[url, heal]`. The
`heal` controller's identity changed on every hook state transition
(busy → busy=false → setModalOpen(true) → user clicks Dismiss →
setModalOpen(false) → ...), and each identity change re-fired the
effect, re-hit HEAD 410, re-called `heal.attempt()`, and re-opened the
modal. Net effect: an artifact whose source file was manually deleted
from the workspace was impossible to close — the user was trapped on
the "no matching file" dialog with refresh providing no escape.

Fix: stash `heal.attempt` in an `attemptRef` and depend only on
`[url]`. The effect now only fires when the URL itself changes (token
re-mint, agent re-registered, etc.) — exactly when re-probing actually
makes sense. Same pattern applied to Chart/Csv/Image/Markdown/Pdf
renderers. See `useArtifactHeal.ts.md` for the hook-side defenses.

## 2026-05-15 — keyed on `updated_at` for live refresh on re-register

The renderer now passes `artifact.updated_at` as the `refreshKey` to
`useArtifactRawUrl`, and the iframe element itself carries
`key={artifact.updated_at}`. When the agent re-registers via
`target_artifact_id`, ChatPanel refetches the artifact (via the
`refreshArtifactFromToolCall` dedup helper), the store upserts the new
`updated_at`, the hook re-mints a fresh token, and the iframe's `src`
changes — so the document and its sibling assets reload with the
latest bytes. Belt-and-braces: the explicit `key=` forces a React
remount even on edge cases where two consecutive mints produce the
same URL string.

## 2026-05-14 — multi-file iframe via token-protected directory URL

Spec: `reference/self_notebook/specs/2026-05-14-artifact-pointer-model-design.md`

The renderer switched from `blob:` URL to a real `iframe src=` pointing at
the token-protected public directory URL minted by `useArtifactRawUrl`.
Why: blob URLs break relative sub-resource resolution, so the previous
design could not support multi-file HTML artifacts (entry html + ./style.css
+ ./data.json etc.) — the whole point of the pointer model. The CSP header
on the entry response (built from the request origin) restricts sub-resource
loading to the same host, so external destinations stay blocked. Combined
with the unchanged `sandbox="allow-scripts"` (no allow-same-origin) the
isolation guarantees are at least as strong as the blob: design.

The `version` prop is gone — there is no version concept under the pointer
model.

# HtmlRenderer.tsx — Security-isolated HTML artifact renderer

## Why it exists

Renders agent-emitted HTML inside an `<iframe>` with a carefully chosen `sandbox` attribute that prevents the untrusted content from escaping into the parent application. This is the most security-sensitive renderer in the set.

## Upstream / Downstream

- **Used by**: `ArtifactColumn` via `React.lazy`, dispatched when `artifact.kind === 'text/html'`. PDF was previously routed here but was split into `PdfRenderer` (C4, 2026-05-09) — see `PdfRenderer.tsx.md` for rationale.
- **Calls**: `rawUrl()` from `@/types/artifact` to set `iframe.src`.

## Security contract

The `sandbox="allow-scripts"` attribute is the heart of this component's threat model:

| Flag | Present? | Rationale |
|------|----------|-----------|
| `allow-scripts` | YES | Agents may emit interactive HTML with JS (charts, forms). Without this, all JS is silently suppressed. |
| `allow-same-origin` | NO | With same-origin, the iframe shares the parent origin and can access parent cookies, localStorage, and DOM — a trivial XSS escape. |
| `allow-top-navigation` | NO | Without this, the iframe cannot redirect the top-level frame (phishing / open-redirect prevention). |
| `allow-popups-to-escape-sandbox` | NO | Without this, any window.open() call from the iframe is also sandboxed. |

The combination of `allow-scripts` **without** `allow-same-origin` is the idiomatic "execute JS in isolation" pattern. The iframe becomes null-origin, which means it cannot perform cross-origin reads of the parent at all.

**Why `src=` instead of `srcdoc=`?** `srcdoc=` content is parsed in the parent origin context, so server-side CSP headers do not apply. Using `src=` means the browser makes a real HTTP request and the FastAPI route can set `Content-Security-Policy: default-src 'none'` on the response, blocking all outbound network calls from the agent HTML even if the sandbox policy is ever relaxed.

## Design decisions

**`referrerPolicy="no-referrer"`** prevents the origin of the parent app from leaking to any destination the agent HTML might attempt to contact (even though `allow-same-origin` is absent, belt-and-suspenders).

**`loading="lazy"`** defers iframe load until it is near the viewport — important since multiple artifact tabs may be rendered simultaneously (even if hidden).

**`bg-white`** — HTML content typically assumes a white background. Using the app's dark background would make most agent-generated pages unreadable.

## Gotchas

**Test this file's sandbox attribute with `HtmlRenderer.test.tsx`.** The test asserts the exact sandbox tokens. If someone adds `allow-same-origin` believing it is harmless, the test will fail and surface the regression.

**CSP on the FastAPI `/raw` endpoint must be verified separately.** This component alone cannot enforce the `default-src 'none'` header — that is a backend concern. If the backend omits the CSP header, network requests from the agent HTML will succeed (limited only by the null-origin sandbox itself, which blocks same-origin reads but not outbound fetches to third-party URLs).
