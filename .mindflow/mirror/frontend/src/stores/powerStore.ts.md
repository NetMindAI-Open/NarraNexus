---
code_file: frontend/src/stores/powerStore.ts
last_verified: 2026-07-23
stub: false
---

# powerStore.ts — Locked Use (prevent sleep) intent

Owns the user's "keep this computer awake" intent; the OS assertion itself
is the Rust `set_prevent_sleep` command ([[power.rs]], caffeinate child).
Persisted (`narra-nexus-power`); `applyOnStartup()` re-asserts an enabled
state after restart because the previous process's assertion died with it —
called once from [[App.tsx]]. State only flips on a CONFIRMED invoke; a
failed command (web mode, non-macOS) leaves the toggle off instead of
lying. UI surface: [[SettingsModal.tsx]] Desktop section (Tauri-only).
Tests: `__tests__/powerStore.test.ts`.
