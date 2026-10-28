---
code_file: src/narranexus/platform/module_system/channel_trigger_map.py
stub: false
last_verified: 2026-09-07
---

## 2026-09-07 — `_resolve_all` 变成纯读

删掉 `_build()` 路径里的 `WorkingSource.register(cls.channel_name)`。解析 trigger 类是
**读**；在读路径上改两个进程级开放枚举，意味着 `WorkingSource("lark")` 能不能解析取决于
有没有人先读过这张表。注册现在只发生在渠道 contribution 建立时
（`channel/contributions.py:register_working_source`），见那份 mirror。

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
  attribute can never drift. Contrast `module_registry` in `module/__init__.py`, which
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

## 2026-09-04 · ingress triggers (batch 3c.3)

Rewritten as `TriggerMapView`, a live Mapping over `ingress.triggers` (host=channels) — no `_TRIGGER_SPECS` table any more. A channel builtin disabled through registry.json is removed from the registry at boot and thus never started; per-channel import isolation is preserved at `TriggerSpec.resolve()` time; `REGISTERED_TRIGGER_CLASS_NAMES` is derived from the static specs (intent), independent of what imports.

The view is a `MutableMapping` only for an explicit override layer (`monkeypatch.setitem(CHANNEL_TRIGGER_MAP, name, fake)` — 22 manyfold ingress tests inject fake triggers that way); overrides shadow registry names; deleting a registry name hides it until something is assigned to it again (`monkeypatch.delitem` + undo round-trips). Real registration is `ingress.triggers` only.

## 2026-09-04 · webhook transport (batch 4c)

`TriggerMapView._build` registers each loaded trigger's channel name as a `WorkingSource` (plugin channels name their own inbound source).

## 2026-09-07 — cache keyed on registry state; unavailable warnings de-duplicated

_build() reuses the resolved dict while the registry's name tuple is unchanged (override/hidden layers are applied on top each time so monkeypatch.setitem/delitem still work); an unavailable trigger is warned about once per (name, error) instead of on every membership test.

## 2026-09-07 — registered_trigger_class_names() from the registry

The registration intent (class names of every host='channels' trigger) is read from the ingress.triggers entries' meta — no import-time constant computed from a platform table before any boot.
