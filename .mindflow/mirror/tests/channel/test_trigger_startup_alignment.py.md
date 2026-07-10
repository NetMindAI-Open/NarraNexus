---
code_file: tests/channel/test_trigger_startup_alignment.py
last_verified: 2026-07-02
---

# test_trigger_startup_alignment.py — rule #7 guard for channel trigger wiring

Kills a recurring outage class: a channel module ships a
`run_*_trigger.py` entrypoint but one startup path never launches it, so
users bind the channel successfully while inbound messages silently never
arrive. History: Slack/Telegram (dev-local.sh only), NarraMessenger
(compose gap), WeChat 2026-07 (absent from both Tauri factories — dmg
users got a dead channel; cloud compose was missing it too).

Design decision: the **filesystem is the source of truth** —
`module/*_module/run_*_trigger.py` glob, not a hand-maintained list — so a
brand-new channel module is guarded the moment its entrypoint file lands.
`state.rs` is checked per-factory (`bundled_services` / `dev_services`)
because a single string match could be dmg-only or dev-only. A pkill check
covers `run.sh stop` leaking stale pollers.

Out of scope: `NarraNexus-deploy/stacks/narranexus-app/compose.yml` (cloud)
is a separate repo; guarded there by `scripts/check_trigger_alignment.sh`
in the deploy repo.
