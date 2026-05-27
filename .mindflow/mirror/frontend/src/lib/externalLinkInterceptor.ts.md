---
code_file: frontend/src/lib/externalLinkInterceptor.ts
last_verified: 2026-05-27
stub: false
---

# externalLinkInterceptor.ts — make `<a target="_blank">` work in Tauri

## Why it exists

In a regular browser, `<a target="_blank">` opens a new tab. In the
Tauri WKWebView (under `tauri.localhost` origin), the webview either
swallows the click or tries to navigate within itself — CSP /
cross-origin blocks the load, no visible error. Every help link in
the dmg ("Getting started", provider docs, Lark/Slack/Telegram
setup hints, artifact fallback link) was silently dead. TODO:
`reference/self_notebook/todo/2026-05-27-dmg-external-links-dead.md`.

This module installs ONE global capturing-phase click listener on
`document`. It walks up from the click target to the nearest `<a>`,
gates on `target="_blank"` plus a safe URL scheme, then routes via
[[tauri]] `openExternal` (plugin-shell `open`). A single interceptor
covers every existing and future external-link site app-wide — no
per-component refactor.

## Upstream / Downstream

- **Installed by**: [[main]] at boot (after Manyfold fragment-auth).
- **Calls**: [[tauri]] `openExternal(url)` → `invoke('plugin:shell|open', ...)`.

## Design decisions

**Capturing phase, not bubbling.** Capturing fires the listener
before any nested React handlers, so `preventDefault()` reliably
suppresses the default `target="_blank"` behavior even when the
anchor sits inside a component with its own onClick.

**Scheme whitelist (http / https / mailto / tel).** Hardcoded set
keeps `javascript:` / `file:` / `data:` / `narranexus:` (our deep
link) off the OS-browser path. `narranexus:` is handled by the
Tauri deep-link plugin, not by user clicks; the others are
attack-vector or nonsensical.

**Modifier-click pass-through.** Ctrl/Cmd-click, middle-click, and
already-prevented events skip the interceptor. The browser's
default handling for those intents (new tab, new window, save) is
already correct and trying to "improve" it would be surprising.

**Browser-mode no-op.** `isTauri()` returns false → uninstaller
returns immediately with a no-op. Browsers' native
`target="_blank"` is fine; the interceptor is exclusively for
Tauri's broken default.

## Gotchas

`addEventListener` dedupes identical `(function, capture)` pairs, so
calling `installExternalLinkInterceptor()` twice during HMR is
safe. The returned uninstaller is provided primarily for tests; in
production main.tsx never calls it.

The walk-up uses `parentElement` (skipping text nodes), so anchors
with nested children (`<a><span><b>...</b></span></a>`) correctly
match when the user clicks the deeply-nested element.
