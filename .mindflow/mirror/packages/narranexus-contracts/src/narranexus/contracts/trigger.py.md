---
code_file: packages/narranexus-contracts/src/narranexus/contracts/trigger.py
last_verified: 2026-09-04
stub: false
---

# contracts/trigger.py — ingress trigger contract (slot `ingress.triggers`)

## Intent

The last three hard-coded "who listens for the outside world" tables (the channel trigger map, the `jobs` worker of the workers supervisor, the A2A server the module runner serves) become one registry of `TriggerSpec`s. The spec names the class lazily (`class_ref = "pkg.mod:Class"`) instead of holding it: an optional dependency missing in one deployment (matrix-nio) isolates that one trigger at resolve time exactly as the old per-channel defensive import did, and the platform never imports a builtin module to know its triggers exist.

`host` says which process owns the instance and therefore which start protocol applies — `channels` (non-blocking `pre_start(db)/start(db)`, the `ChannelTriggerBase` shape), `workers` (`cls(**kwargs)`, blocking `start()` until `stop()`), `api` (an HTTP server built and `run()` on demand). The `Trigger` Protocol deliberately only promises `stop()`; the host-specific start shapes are documented, not forced into one signature.

## Consumers

`module/channel_trigger_map.TriggerMapView` (host=channels), `module/run_worker_supervisor.trigger_worker_specs` (host=workers, builtin owners keep the bare name so `--only/--exclude jobs` still works), `module/module_runner._a2a_server_class` (host=api). Specs for the builtins live in `module/contributions.TRIGGER_SPECS`; the builtin manifests name them.
