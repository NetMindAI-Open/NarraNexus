---
code_file: tauri/src-tauri/src/commands/mod.rs
last_verified: 2026-07-23
---

# mod.rs — Module declaration for the commands directory

Just module declarations: `auth`, `config`, `deep_link`, `file_download`,
`health`, `netmind_oauth`, `notify`, `power`, `service`, `tray`, `updater`, `artifact_fetch`,
`office_watch_scheme`. No logic. Each module exposes one or more
`#[tauri::command]` functions registered in `lib.rs::run`'s `invoke_handler!`
(or, for `office_watch_scheme`, a custom-scheme handler registered on the
builder). `deep_link` was added with the `narranexus://` one-click-install
handoff. `artifact_fetch` (2026-05-27) proxies artifact bytes through Rust to
dodge WKWebView's mixed-content blocker in the dmg — see [[artifact_fetch.rs]].
`netmind_oauth` (2026-07-14) is the desktop NetMind ("Power") OAuth bridge —
see [[netmind_oauth.rs]]. `power` (2026-07-23) holds the Locked Use prevent-sleep assertion — see
[[power.rs]]. `notify` (2026-07-23) posts OS notifications when an agent finishes
replying (#44) — see [[notify.rs]]. `office_watch_scheme` (2026-07-14) serves the
`officewatch://` custom scheme for live Office preview, same mixed-content
dodge — see [[office_watch_scheme.rs]].
