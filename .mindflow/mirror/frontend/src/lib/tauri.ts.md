---
code_file: frontend/src/lib/tauri.ts
last_verified: 2026-05-22
stub: false
---

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
