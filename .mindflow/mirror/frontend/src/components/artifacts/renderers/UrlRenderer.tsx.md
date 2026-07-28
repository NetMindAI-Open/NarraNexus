---
code_file: frontend/src/components/artifacts/renderers/UrlRenderer.tsx
last_verified: 2026-07-22
stub: false
---

# UrlRenderer.tsx — renderer for URL-tab artifacts (application/x-url)

## Why it exists

Renders an `application/x-url` artifact. The entry file is a JSON doc
(`UrlArtifactDoc`) holding the URL + the server-side embed verdict; this
renderer fetches it through the token-authed raw route, then:

- `effective_mode === 'iframe'` → iframes the EXTERNAL url directly.
- `effective_mode === 'stream'` → fallback card (open-in-new-window). This is
  the seam where the future streaming renderer (方案三) plugs in.
- always shows a mode toggle so the user can override a wrong verdict; the
  override persists via `artifactsApi.setEmbedMode` and a `refreshKey` bump
  re-mints the token + refetches the doc.

## Design decisions

- The iframe `src` is the external URL, not our backend, so HtmlRenderer's
  Tauri mixed-content blob dodge is unnecessary for https targets — one code
  path serves both run modes (铁律 #7).
- iframe sandbox is `allow-scripts allow-same-origin allow-forms allow-popups
  allow-popups-to-escape-sandbox` — a real third-party site needs its own
  origin/storage/forms to function. `allow-same-origin` is safe ONLY because a
  URL tab is cross-origin third-party content; a tab pointing at our OWN origin
  would become a same-origin scriptable iframe reaching the app token, so the
  backend (`url_artifact._reject_self_origin`) refuses to open one. The sandbox
  safety DEPENDS on that guard — don't copy it to a same-origin renderer.
- Navigation (2026-07-22, after a revert): a cross-origin page's link clicks
  are invisible to us (same-origin policy) — we can neither read the target
  URL nor redirect it into a new in-app tab. A `target="_blank"` link has only
  two possible fates: open a new OS-browser tab, or be blocked. The sandbox
  KEEPS `allow-popups allow-popups-to-escape-sandbox` so such links WORK (open
  in the browser) — a dead link is worse than one that opens externally. (An
  earlier same-day change dropped the popup flags to stop the "jump out", but
  that blocked target=_blank links entirely — reverted.) Same-frame links
  navigate in the tab. True "every link opens as an in-app tab" is a
  streaming-browser capability, not an iframe one. `allow-top-navigation` is
  NEVER granted (an embedded page must not be able to navigate our whole app).
  The mode toggle labels are "Inline" (iframe) / "External" (the
  open-in-browser fallback card, formerly the misleading "Full").
- RUN-MODE (铁律 #7): the popup/open-in-browser behavior above holds in
  BROWSER mode only. On the packaged Tauri DMG, WKWebView blocks popups (see
  netmind_oauth.rs), so URL-tab target=_blank links are likely STILL dead on
  desktop until a Tauri new-window handler routes them to the OS browser (or
  the streaming browser lands). Follow-up tracked (author-local todo).
- The doc fetch uses `fetchArtifactText` like the other text renderers
  (Csv/Markdown/Chart) — same pattern, same Tauri behavior.

## Gotcha

`effectiveEmbedMode` (in types/artifact.ts) collapses recommend + override;
don't read `recommended` directly in the renderer or a user override is
ignored.
