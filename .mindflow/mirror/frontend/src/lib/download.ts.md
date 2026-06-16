---
code_file: frontend/src/lib/download.ts
last_verified: 2026-06-16
stub: false
---

# download.ts — Cross-surface file download utility

## Why it exists

Two download surfaces were silently broken when using the standard
`<a href download>` approach:

1. **Tauri DMG** — the webview origin is `https://tauri.localhost`
   (HTTPS) while the backend serves on `http://localhost:8000` (HTTP).
   WKWebView classifies HTTP navigations initiated from an HTTPS
   document as "active mixed content" and blocks them silently. Even
   if the request got through, the `download` attribute is ignored for
   cross-origin URLs in all modern browsers.

2. **Local browser** (`bash run.sh`, Vite `:5173` → backend `:8000`)
   — cross-origin, so the `download` attribute is silently ignored
   (browser navigates instead of saving). Workspace files additionally
   require `X-User-Id` / `Authorization` headers that an `<a>` element
   cannot attach, causing a 401.

The fix is a single `downloadFile({ url, filename, authHeaders? })`
function that picks the correct strategy per runtime surface:

- **Tauri path**: delegates to `downloadFileViaTauri()` from
  `lib/tauri.ts`, which invokes the Rust `download_file_via_backend`
  command. Rust-originated HTTP is immune to WKWebView's mixed-content
  blocker. The command saves the file to `~/Downloads` and returns the
  absolute path, which `downloadFile` surfaces via `window.alert`.
- **Browser path**: issues `fetch(url, { headers: authHeaders })`,
  converts the response to a Blob, creates an object URL, appends a
  programmatic `<a download>` to the body, clicks it, and immediately
  revokes the object URL. The `fetch()` call carries any auth headers
  and lands the bytes in memory first, bypassing the cross-origin
  `<a download>` restriction.

## This file does not do

- Chart image export (PNG/JPEG from ECharts canvas) — that uses a
  `data:` URL and a programmatic `<a download>` directly in
  `ArtifactDownloadMenu`. That path does not hit backend endpoints and
  is not cross-origin, so no helper is needed.
- Auth header generation — callers pass `api.getAuthHeaders()` for
  workspace files; artifact raw URLs are public (token in query string)
  so `authHeaders` is omitted.

## Upstream / Downstream

- **Called by**: `ArtifactDownloadMenu.tsx` (for the "Download
  original" entry; artifact URLs are public, so `authHeaders` is
  omitted) and `FileUpload.tsx` (for per-file workspace Download
  buttons; `api.getAuthHeaders()` is passed as `authHeaders` because
  workspace endpoints are auth-gated).
- **Depends on**: `lib/tauri.ts` (`isTauri`, `downloadFileViaTauri`).
  The browser fetch path has no external dependencies.

## Design decisions

- **Single entry point, surface-detected internally.** Callers do not
  branch on `isTauri()` themselves — `downloadFile()` handles it. This
  keeps surface-specific logic in one place and makes callers uniform.
- **`authHeaders` is optional at the interface level.** Artifact URLs
  encode access tokens in the query string; forcing callers to always
  pass headers would be misleading. Passing `undefined` naturally
  omits the `headers` option from `fetch()`.
- **Tauri errors surfaced via `window.alert`.** The Tauri path catches
  errors from the Rust command and alerts the user. This is intentional
  for now: the download button is a low-stakes UI control and the
  simpler alert avoids needing a toast/notification system in this
  utility.

## Gotchas

- **`isTauri()` race at mount**: if `isTauri()` returns `true` but the
  Tauri IPC channel is not yet attached, `downloadFileViaTauri` returns
  `null`. `downloadFile` handles this by returning silently. The
  "Download" button will not be visible in that state, so this is a
  benign edge case.
- **Browser `fetch` requires CORS.** On the local browser surface the
  backend must include `Access-Control-Allow-Origin` headers for the
  workspace and artifact endpoints. This is already configured; do not
  add restrictive CORS rules that would block credentialed requests.

## Related constraints

- See `tauri/src-tauri/src/commands/file_download.rs` for the Rust
  side's SSRF guard (only loopback host, port 8000, and the two
  allowed path prefixes are accepted).
- Mirrors the pattern established by `fetchArtifactViaTauri` /
  `artifact_fetch.rs` for Rust-proxied HTTP.
