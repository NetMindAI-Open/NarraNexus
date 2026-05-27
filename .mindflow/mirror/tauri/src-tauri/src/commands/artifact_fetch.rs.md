---
code_file: tauri/src-tauri/src/commands/artifact_fetch.rs
last_verified: 2026-05-27
stub: false
---

# artifact_fetch.rs — Rust-side artifact bytes proxy for the dmg

## Why it exists

In the dmg the Tauri webview origin is `https://tauri.localhost` (HTTPS)
while the local backend serves on `http://localhost:8000` (HTTP).
WKWebView classifies any HTTP subresource loaded from an HTTPS document
as "active mixed content" and blocks it silently — both iframe loads
*and* `fetch()` from JS. The artifact panel rendered as a white iframe
for every test artifact (P0 reported 2026-05-27).

Header-level fixes (`X-Frame-Options` removal, `frame-ancestors`,
`Cross-Origin-Resource-Policy: cross-origin`) are necessary but **not
sufficient** — they only matter once the request actually reaches the
server. Mixed content blocking fires earlier, in the network layer.

HTTP requests originated by **Rust** (not the WKWebView) are immune
to the mixed-content blocker. This command pulls the artifact bytes
via reqwest and ships them back to the frontend as base64 over the IPC
channel. The frontend reconstructs a `Blob` and uses a `blob:` URL as
the iframe `src`. blob URLs inherit the creator document's origin
(`https://tauri.localhost`), so the iframe load is same-origin — the
mixed-content rule never applies.

## Surface

`fetch_artifact_via_backend(url: String) -> Result<ArtifactBytes, String>`

`ArtifactBytes`:
- `status: u16` — backend HTTP status (frontend checks `=== 200`).
- `content_type: String` — verbatim `Content-Type` header, defaults
  to `application/octet-stream` if absent.
- `bytes_base64: String` — base64-encoded body. IPC only ships JSON,
  so binary bytes need this round-trip.

## Security: SSRF guard

The validator (`validate(&str) -> Result<Url, String>`) enforces a
strict whitelist before reqwest sees the URL:

- scheme ∈ {`http`, `https`}
- host ∈ {`localhost`, `127.0.0.1`, `::1`}
- port ∈ {`8000`} (the bundled backend port — keep this list in sync
  with `state::bundled_services` if we ever move the backend off 8000)
- path prefix starts with `/api/public/artifacts/`

Anything else returns an `Err`. Without this guard, a malicious
artifact URL passed from JS could turn the command into a generic
intranet-scanner: "give me back whatever's at
`http://10.0.0.1/secret`". The whitelist confines the command to its
sole intended target.

## Upstream / Downstream

- **Registered in**: `lib.rs::run()` `invoke_handler!` block.
- **Called by**: `frontend/src/lib/tauri.ts::fetchArtifactViaTauri()`,
  which exposes a JS-side helper that returns a blob URL.
- **Used in**: `frontend/src/components/artifacts/renderers/HtmlRenderer.tsx`
  blob-fetch effect (Tauri path tried first; HTTP fetch is the
  fallback).

## Dependencies

- `reqwest 0.12` with `default-features = false` + `http2`. No TLS
  features — localhost-only. Already pulled transitively by
  `tauri-plugin-updater`, so declaring it direct adds no compile cost.
- `base64 0.22` for the body envelope.
- `url 2` for `validate`'s parsing — same parser reqwest uses, so
  what we whitelist and what reqwest actually requests cannot diverge.

## Gotchas

- **15s timeout**: matches the existing `useArtifactRawUrl` patience.
  Tight enough that a stuck backend doesn't hang the iframe forever,
  loose enough to survive a slow first artifact read.
- **Frontend treats non-200 as "fall back to HTTP fetch"** — see
  `tauri.ts::fetchArtifactViaTauri`. So a transient 5xx from the
  backend doesn't surface as a hard error; the HTTP path retries.
- **The bundled backend port is hardcoded to 8000** here AND in
  `port_preflight.rs`. If we ever switch backend to a dynamic port
  (the planned step 3 of the port-collision plan), update both.
- **Devtools feature** lives in `Cargo.toml` (`tauri = { features =
  [..., "devtools"] }`) — enabled in the same change so future
  artifact-rendering bugs can be inspected directly in Safari Web
  Inspector, without another rebuild cycle.
