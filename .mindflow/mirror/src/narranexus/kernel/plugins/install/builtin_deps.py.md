---
code_file: src/narranexus/kernel/plugins/install/builtin_deps.py
last_verified: 2026-09-07
stub: false
---

# install/builtin_deps.py — on-demand dependencies for builtins

## Intent

Spec §843: heavy builtins declare `install.deps: on_demand` with `backend.pip` (what to install) and `backend.imports` (what proves it is there). At boot — and at backend import (`register_builtins_for_import`) — the imports are probed; a missing one triggers a wheels-only install into `~/.narranexus/plugin-deps/<id>`, appended to `sys.path` (a builtin's code lives in its own workspace package `narranexus_plugins.<pkg>` — plus the engine's `narranexus.platform` for the seams it uses — and NOT in the synthetic `nxplugins.<id>` namespace the plugin finder serves, so the finder's per-plugin dependency routing, which keys off the importing module's `nxplugins.` package, cannot apply to it). A failed install, or cloud mode (images bake their dependencies), yields `deps_missing`: the boot removes the builtin's contributions and continues without it; the factory lists the reason and `POST /builtin/{id}/install-deps` retries.

Today only `builtin.channels.lark` (lark-oapi) declares this, and lark-oapi is still in the base `dependencies` — so nothing installs on current builds; the mechanism is what lets a slim distribution drop it (a deploy-repo lockstep change). `home_assistant` needs only core deps (aiohttp) and stays eager.

## 2026-09-07 — ensure_builtin_deps(install=False)

Probe-only mode: reports deps_missing without running the package installer — what the backend's import-time registration uses; the lifespan boot and the factory's install-deps endpoint still install.
