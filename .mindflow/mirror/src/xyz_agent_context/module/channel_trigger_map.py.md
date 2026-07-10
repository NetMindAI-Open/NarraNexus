---
code_file: src/xyz_agent_context/module/channel_trigger_map.py
stub: false
last_verified: 2026-07-08
---

## Why it exists

Single source of truth mapping `channel_name -> ChannelTriggerBase subclass`,
consumed by the consolidated supervisor (`run_channel_triggers`) to instantiate
every IM channel in ONE process. Born from the 2026-07-08 trigger-consolidation
(six `run_*_trigger.py` processes → one supervisor).

## Design decisions

- **Lives in `module/`, NOT `channel/`.** The trigger subclasses live under
  `module/*_module/`; `channel/` is a lower layer. Putting the map in `channel/`
  would invert the dependency and re-enter the circular import that
  `channel_trigger_base` already documents (module → channel → runtime →
  module). The supervisor is a top-level entrypoint, so importing from `module`
  is fine.
- **Key derived from `cls.channel_name`, not hand-written.** `CHANNEL_TRIGGER_MAP`
  keys off each class's own `channel_name`, so the map key and the class
  attribute can never drift. Contrast `MODULE_MAP` in `module/__init__.py`, which
  hand-writes keys.
- **Defensive per-channel import.** Classes are imported one-by-one from
  `_TRIGGER_SPECS` (a `(module_path, class_name)` list), NOT with top-level
  `import`s. A channel whose optional dependency is missing (e.g. `matrix-nio`
  for the NarraMessenger Matrix adapter) is logged and SKIPPED — it does not take
  down the other five. This extends the supervisor's per-channel startup
  isolation down to import time; eager top-level imports would let one channel's
  ImportError crash the whole consolidated process, a regression versus the old
  one-process-per-channel layout.
- **`REGISTERED_TRIGGER_CLASS_NAMES` = registration intent.** The guard test
  checks on-disk `ChannelTriggerBase` subclasses against this set (derived from
  `_TRIGGER_SPECS`), NOT the runtime `CHANNEL_TRIGGER_MAP`, so a channel shipped
  without being registered still fails CI even when its optional dep is absent
  locally — while a merely-missing dep does not read as "forgot to register."
- **`narramessenger` → `MatrixTrigger`.** The channel is served by the
  Direct-Matrix adapter (`matrix_trigger.MatrixTrigger`, `channel_name="narramessenger"`);
  the old gateway `NarramessengerTrigger` was retired.
- **Add a channel = add one line to `_TRIGGER_SPECS`.** Nothing in the supervisor
  changes.

## Upstream / downstream

- **Upstream**: `run_channel_triggers` (the only consumer).
- **Downstream**: the six trigger subclasses (Lark / Slack / Telegram / Discord /
  WeChat / Matrix-for-narramessenger).

## Gotchas

- Importing this module imports every registered trigger module. Keep it out of
  hot import paths (backend, MCP server) — only the supervisor should import it.
