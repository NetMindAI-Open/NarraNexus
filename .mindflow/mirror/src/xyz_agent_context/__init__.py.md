---
code_file: src/xyz_agent_context/__init__.py
last_verified: 2026-09-07
stub: false
---

# xyz_agent_context/__init__.py — the one-release alias of narranexus.platform (D8)

## Intent

Batch 6a moved every domain package to `narranexus.platform` (`module` → `module_system`). This package exists so the old import spelling keeps working for exactly one release: a `sys.meta_path` finder (`_AliasFinder`, inserted first) answers every `xyz_agent_context.<path>` with the SAME module object as `narranexus.platform.<path>` (`_AliasLoader.create_module` returns the imported target, so monkeypatches, `isinstance` and singletons agree), the root re-exports the platform root, and one `DeprecationWarning` per process names the replacement. `_AliasLoader` also forwards the code-access API (`get_code` / `get_source` / `is_package` / `get_filename`) to the target's real loader: `python -m xyz_agent_context.<path>` goes through `runpy`, which never imports the module but asks the loader for its code — without the forwarding every `-m` entrypoint the deploy repo uses (workers supervisor, model-sync, executor, sqlite proxy) died before any application code ran. Physical shim files exist only where something references the path on disk (by-path launchers, the deploy gate scripts); everything else resolves through the finder.

## Expiry

`REMOVED_AT` is the host version at which importing the alias raises `ImportError` (`_refuse_if_expired`, read through `compat.host_version()`): a compat layer without a removal hook is exactly the shim the charter forbids, and this one competes with the plugin importer for `sys.meta_path[0]`, so forgetting to delete it must fail loudly rather than linger.

## Removal

Delete `src/xyz_agent_context/` (and the `packages` entry in pyproject) in the release after the deploy repo switched its compose entrypoints — `docs/PLUGIN_BATCH6_DEPLOY_LOCKSTEP.md` lists them.
