---
code_file: frontend/src/components/system/ServiceCard.tsx
last_verified: 2026-04-10
---

# ServiceCard.tsx — Status card for one backend service

Shows an animated status dot (pings for healthy/running/starting, static for
crashed/stopped), service label, port number, last error, and an optional
restart button.

Pure display component. All state lives in the System page parent. The restart
button calls `onRestart` which in Tauri mode maps to the `restart_service`
IPC command.

Used by: System page (one card per entry in `OverallHealth.services`).
