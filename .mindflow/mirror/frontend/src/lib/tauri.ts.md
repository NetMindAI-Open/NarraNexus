---
code_file: frontend/src/lib/tauri.ts
last_verified: 2026-07-22
stub: false
---

## 2026-07-22 — exported invokeTauri(); events now rely on withGlobalTauri

Added `invokeTauri<T>(cmd, args)` — a generic typed wrapper over the private
`_getInvoke()` (`__TAURI_INTERNALS__.invoke`, no `@tauri-apps/api` npm dep).
Extracted so [[platform.ts]] can drive its desktop bridge through the ONE invoke
path proven to work in the packaged DMG; platform.ts previously did
`import('@tauri-apps/api/core')`, which — since that package isn't installed —
bundled as a bare specifier the webview can't resolve and threw at runtime.

`listenTauri` / `listenUpdaterState` still read `window.__TAURI__.event.listen`
(there is no internals-only event API without hand-rolling the `plugin:event|*`
protocol — rule #9 says don't). To make that global real, `tauri.conf.json` now
sets `app.withGlobalTauri: true`. That injects `window.__TAURI__` (with
`event.listen` + `core.invoke`) in the desktop webview, so both event
subscribers work; invoke still prefers `__TAURI_INTERNALS__`, and the NetMind
OAuth poll fallback is unaffected (belt-and-suspenders, not regressed).

## 2026-07-13 — openNetmindOAuth + takeNetmindOAuthResult wrappers

Added two thin invoke wrappers (no-op outside Tauri) for desktop NetMind OAuth:
`openNetmindOAuth(url)` starts the flow; `takeNetmindOAuthResult()` drains the
buffered `{code,state}` result. [[useNetmindAuth.ts]] `startOAuth` opens then
polls the latter (poll-based delivery — does NOT use `listenTauri`/events, which
need `window.__TAURI__` and can no-op). Rust side: [[netmind_oauth.rs]].

## 2026-05-27 — unified auto-updater wrappers (replaces `checkForUpdates`)

The single-IPC `checkForUpdates()` was retired in favour of four
small wrappers that mirror the unified state machine in
[[updater.rs]]:

| wrapper | shape | when |
|---------|-------|------|
| `kickUpdaterCheck()` | `Promise<void>` | trigger the full check → download → install pipeline (returns immediately; progress arrives via events) |
| `getUpdaterState()` | `Promise<UpdaterState \| null>` | snapshot on mount so the store recovers state if a startup-auto pipeline already transitioned before React mounted |
| `restartForUpdate()` | `Promise<void>` | gated on `state.kind === "ready"` |
| `listenUpdaterState(handler)` | `Promise<() => void \| null>` | subscribe to `updater:state` events; intended for [[updaterStore.ts]] only |

Plus the `UpdaterState` TypeScript discriminated union type — kept
in `lib/tauri.ts` (not the store) so any consumer can import the
type without pulling Zustand. Hand-synced with the Rust enum;
change one, change the other.

Removed: `checkForUpdates()` (old API). Callers updated:
[[SettingsPage.tsx]] now uses the store + `kickUpdaterCheck` /
`restartForUpdate`.

## 2026-06-16 — downloadFileViaTauri(url, filename, headers?)

New helper that invokes the `download_file_via_backend` Rust command
(see [[file_download.rs]]). Saves the file to the OS Downloads folder
(`~/Downloads`) via Rust reqwest, which is immune to WKWebView's
mixed-content blocker. Returns the absolute saved path on success, or
`null` when not in Tauri / IPC channel missing. Throws a string error
when the Rust command returns an error (HTTP failure, filesystem write
error, etc.). Callers (i.e. `downloadFile()` in `lib/download.ts`)
catch Rust errors and surface them via `window.alert`.

Sibling of `fetchArtifactViaTauri` — both route through Rust to bypass
the mixed-content block; the distinction is that `fetchArtifactViaTauri`
returns bytes to JS for in-page rendering, while `downloadFileViaTauri`
saves to disk for the user to open.

## 2026-05-27 — fetchArtifactViaTauri(url)

New helper that invokes the `fetch_artifact_via_backend` Rust command
(see [[artifact_fetch.rs]]) to pull artifact bytes through Rust's
reqwest instead of the JS `fetch()`. Reason: in the dmg the webview
parent is `https://tauri.localhost` (HTTPS) and the backend is
`http://localhost:8000` (HTTP) — WKWebView blocks the latter as
"active mixed content" and the artifact panel rendered as a white
iframe (P0 2026-05-27). Rust-originated HTTP isn't subject to that
block. The helper decodes the base64 body the Rust side ships and
returns a `blob:` URL the caller can set on an iframe; the blob URL
is same-origin to the parent so the iframe load isn't itself mixed
content.

Returns `null` when not in Tauri / IPC missing / Rust returned
non-200 / IPC errored, so callers can transparently fall back to the
plain `fetch()` path (HtmlRenderer's blob-fetch effect does exactly
that). Caller is responsible for `URL.revokeObjectURL()` on the
returned blob URL (HtmlRenderer revokes in its effect cleanup).

## 2026-05-27 — openExternal(url)

New helper that invokes `plugin:shell|open` so `<a target="_blank">`
clicks intercepted by [[externalLinkInterceptor]] actually open in
the OS browser. Uses the same `__TAURI__.invoke` channel as the
other helpers (no npm dep). Capability `shell:allow-open` and config
`"shell": { "open": true }` are already wired in
`tauri/src-tauri/capabilities/default.json` + `tauri.conf.json`.
Browser mode returns false (caller's interceptor is itself a no-op
in browser, so this path is dead code there but kept symmetric).

## 2026-05-22 — checkForUpdates()

Added `checkForUpdates()` — invokes the Rust `check_and_install_update` command
(via the same `__TAURI_INTERNALS__.invoke` pattern, no npm dep) for the Settings
"Check for updates" button. Returns `'up_to_date'` / `'installed:<v>'`; null in
web mode. The actual update logic + the startup auto-check live in Rust
(`commands/updater.rs`).

# tauri.ts — thin browser-side wrapper for Tauri v2 IPC

## Why it exists

The frontend ships in two runtimes: a normal browser (cloud-web) and the
packaged Tauri desktop app. Desktop-only capabilities — tray badge, Claude
Code OAuth login/logout, deep-link handoff, window focus/blur events — are
exposed by the Rust side as `#[tauri::command]`s. This module is the single
place React reaches them, and every export is a **safe no-op in the browser**,
so callers never have to branch on runtime themselves.

## Upstream / Downstream

**Used by:** dashboard / settings UI (tray badge, Claude login flow), and
`App.tsx` for the deep-link handoff (`consumePendingDeepLink` + the
`deep-link-received` event).

**Talks to:** the Rust commands in `tauri/src-tauri/src/commands/` (`tray`,
`auth`, `deep_link`) via the injected global invoke.

## Design decisions

**No `@tauri-apps/api` npm dependency.** Calls go through the global
`window.__TAURI_INTERNALS__.invoke` (with a `window.__TAURI__.core.invoke`
fallback) that Tauri v2 injects at runtime. `_getInvoke()` resolves whichever
is present; with neither, every helper returns null/false. This keeps the web
bundle free of a desktop-only package.

**`isTauri()` is layered.** It checks the injected globals first, then falls
back to the `tauri:` protocol / `tauri.localhost` hostname — covering
early-mount timing where the globals may not be attached yet.

**Failure policy splits by stakes.** `setTrayBadge` clamps to 0–999 and
swallows errors (a missing tray must never break the app); the Claude
login/logout helpers instead let throws propagate so the settings UI can
surface them.

## Gotchas

`consumePendingDeepLink()` drains a URL the Rust side buffered when the OS
delivered a `narranexus://` link *before* React mounted. Tauri events fired
before any listener exists are dropped, so `App.tsx` wires both paths: this
drain on first mount, and a live `deep-link-received` event listener for the
hot case. See `tauri/src-tauri/src/commands/deep_link.rs` for the buffer
rationale.
