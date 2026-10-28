---
code_file: plugins/builtin.frameworks.nexus_power/src/narranexus_plugins/frameworks_nexus_power/core/extension_points.py
last_verified: 2026-09-07
stub: false
---

# nexus_power/extension_points.py — the loop's strategy seats as slots

## Intent

The third vertical grain of the pipeline (spec §484): platform stages → act-stage framework → seams inside the framework. Five NexusPower protocols become slots under the plugin's own namespace (`builtin.frameworks.nexus_power.{stop,compaction,projector,expression,policy}` — the manifest ownership rule requires the plugin id prefix, so the spec's short `nexus_power.stop` reads as this path). The loop's own classes (`NoMoreActionsStop`, `ToolResultPruner`, `PassthroughProjector`, `ExpressionContract`, the three policy layers in their historical check order) are the default providers; another plugin provides an alternative into the same slot and configuration binds it (`NX_BIND__builtin__frameworks__nexus_power__stop=<provider>`, comma list for the many-arity policy seat). Providing alone never rebinds a one-arity seat — binding is configuration, exactly as the slot tree promises.

A provider is `Callable[[SeatContext], impl]`; `SeatContext` carries the turn options, workspace, tool context, provider profile and (for the projector) the harness-inserted base messages + tail renderer. `resolve_one/resolve_many` apply env-over-default and fail loud on an unknown provider (`UnknownEntry`): a bound name nobody provides is a configuration error, never a silent fallback.

## Gotchas

- Registered twice on purpose: the manifest (booted hosts) and `ensure_registered` (any process running the loop without a plugin boot — the executor subprocess runner) — `Registry.register` and `HookCaller.add` are idempotent on same owner + name. Third-party seat providers therefore reach the loop only when their plugin is loaded in the process that runs it; the subprocess runner sees the defaults and env bindings.
- The kernel `bindings.resolve` needs every one-slot in the tree to have a default; the seats use a small local resolver over `parse_env` + `slot.default` for the same layering (env > default) without that global precondition.

## 2026-09-07 — seat providers register through the registry's conflict rule

ensure_registered always calls register_contribution: same object / same owner is a no-op, a different owner under the same name is RegistryConflict — 'skip if the name exists' let whoever registered first become workspace_confinement.

## 2026-09-07 — seats under the framework slot; bindings through the kernel

NAMESPACE is turn.pipeline.act.framework.nexus_power (OWNER stays the plugin id): the seats are descendants of the framework slot, so the slot tree's nesting rule and every binding layer apply. resolve_one / resolve_many use kernel.plugins.bound (bound_entry / bound_entries) — the private env-only binding parser, bound_provider(s) and the per-turn ensure_registered are gone; registration happens once at boot.

## 2026-09-07（round-2 G2-I5）— seat symbols follow API_POLICY §8

`STOP_DEFAULT` / `COMPACTION_DEFAULT` / `PROJECTOR_DEFAULT` / `EXPRESSION_DEFAULT` /
`POLICY_LAYERS` → `STOP_CONTRIBUTION` / `COMPACTION_CONTRIBUTION` / `PROJECTOR_CONTRIBUTION` /
`EXPRESSION_CONTRIBUTION` / `POLICY_CONTRIBUTIONS`. §8 says a one-arity slot is filled by
`CONTRIBUTION` and a many-arity one by `CONTRIBUTIONS`; five seats live in this one module, so the
seat name prefixes the §8 word (the rule §8 gained in the same change). The `_DEFAULT` suffix was a
sixth naming vocabulary that said nothing about arity. Symbol names only — seat contribution names
(`no_more_actions`, `tool_result_pruner`, …), which bindings refer to, are untouched.
