---
code_file: tauri/src-tauri/src/commands/file_download.rs
last_verified: 2026-06-16
stub: false
---

# file_download.rs — Save a backend file to the OS Downloads folder

## Why it exists

Two local download surfaces were broken with the standard
`<a href download>` approach:

1. **Tauri DMG**: the webview origin is `https://tauri.localhost`
   (HTTPS) while the backend serves on `http://localhost:8000` (HTTP).
   WKWebView classifies HTTP navigations from an HTTPS document as
   "active mixed content" and blocks them silently. The `download`
   attribute is also ignored for cross-origin URLs in all modern
   browsers.

2. **Local browser** (`bash run.sh`, Vite `:5173` → backend `:8000`):
   cross-origin, so the `download` attribute is silently ignored.
   Workspace files additionally require `X-User-Id` / `Authorization`
   headers that `<a>` elements cannot send.

HTTP requests originated by Rust are not subject to WKWebView's
mixed-content rules. This command fetches file bytes via `reqwest`,
optionally attaching auth headers, writes them to the OS Downloads
directory, and returns the absolute saved path so the frontend can
display a "Saved to: …" confirmation.

## This file does not do

- Streaming large files progressively — bytes are buffered in memory
  before writing. This is acceptable for the artifact and workspace
  file sizes this command targets.
- Serving bytes back to the JS side for in-page rendering — that is
  `artifact_fetch.rs`'s job (returns base64 for iframe display). This
  command always writes to disk.

## Upstream / Downstream

- **Registered in**: `lib.rs` `invoke_handler!` macro as
  `commands::file_download::download_file_via_backend`.
- **Called by**: `frontend/src/lib/tauri.ts::downloadFileViaTauri()`,
  which is in turn called by `frontend/src/lib/download.ts::downloadFile()`
  when running inside Tauri.
- **Depends on**: `reqwest` (HTTP), `dirs` crate (resolves
  `~/Downloads` without a new Tauri plugin), `url` (URL parsing for
  the SSRF validator).

## Security: SSRF guard

Mirrors the guard in `artifact_fetch.rs`. Before `reqwest` sees the
URL the `validate()` function enforces:

- scheme ∈ `{http, https}`
- host ∈ `{localhost, 127.0.0.1, ::1}`
- port ∈ `{8000}`
- path prefix ∈ `{/api/public/artifacts/, /api/agents/}`

This confines the command to its two intended targets (public artifact
files and auth-gated workspace files). An attacker-controlled URL
coming from JS cannot use this command to scan the intranet.

**Keep the allowed-prefix list in sync**: if the workspace file raw
endpoint or artifact raw endpoint path changes, update
`ALLOWED_PATH_PREFIXES` here and in `artifact_fetch.rs`.

## Filename safety

`safe_basename()` strips directory components (prevents path traversal
in the filename), replaces OS-illegal characters (`\0 : * ? " < > |`),
trims trailing dots/spaces (Windows compat), and caps to 200 chars. If
the sanitised result is empty, it falls back to `"download"`.

`resolve_output_path()` avoids overwriting existing files by appending
` (n)` (up to 99) before the extension — same UX as macOS Finder.

## Design decisions

- **`dirs` crate, not a Tauri plugin.** `dirs::download_dir()` resolves
  the OS Downloads directory without the overhead of a new Tauri plugin.
  Falls back to `dirs::home_dir()` if the platform has no dedicated
  Downloads folder.
- **30s timeout.** Workspace files can be large; 30 s is generous
  compared to the 15 s used by `artifact_fetch.rs` for in-page rendering.
- **Sibling of `artifact_fetch.rs`.** Both commands proxy the same
  backend server via reqwest to bypass the WKWebView mixed-content
  block. The difference: `artifact_fetch.rs` returns bytes to JS for
  rendering; this command saves to disk for downloading.

## Gotchas

- **Port 8000 is hardcoded** here and in `artifact_fetch.rs` and
  `port_preflight.rs`. If the backend port ever becomes dynamic, all
  three locations need updating.
- **`authHeaders` is `Option<HashMap<String, String>>`** — artifact
  public URLs carry an access token in the query string and pass
  `None`; workspace URLs need `X-User-Id` + `Authorization`.
- **Non-UTF-8 saved paths** return an error string. In practice macOS
  paths are always UTF-8, but the code handles it gracefully rather
  than unwrapping.
- **The 99-attempt filename collision guard** will return an error if
  you already have 99 copies of the same filename in Downloads. This
  is a theoretical limit; the alert from `downloadFile()` will surface
  the error string.

## Related constraints

- Binding rule #12: this command writes to the filesystem (OS
  Downloads directory). It is authorized because it is initiated by an
  authenticated user action in the frontend, not by an agent.
- See `frontend/src/lib/download.ts` for the JS orchestration layer
  that decides when to call this vs. the browser fetch path.
