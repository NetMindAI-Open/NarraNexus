---
code_file: frontend/src/lib/desktopNotify.ts
last_verified: 2026-07-23
stub: false
---

# desktopNotify.ts — desktop OS notification bridge (#44)

`notifyAgentReplyCompleted(agentName)`: Tauri build → invoke the Rust
`notify_completion` command ([[notify.rs]]) through the standard
`__TAURI_INTERNALS__` channel ([[tauri.ts]] — never `@tauri-apps/*` npm);
web mode → no-op. Best-effort by contract: every failure is swallowed, a
notification must never break the chat flow. Caller and its gating
(unfocused window, not cancelled, was streaming): [[chatStore.ts]]
`stopStreaming`.
