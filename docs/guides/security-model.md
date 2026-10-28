# Plugin security model

Plugins run in-process with the host. The platform does not sandbox them; it
makes their reach **declared, visible, isolated and reversible**.

## Declared

`permissions` in the manifest (`network`, `filesystem`, `subprocess`, `env`)
are shown on install and on the factory page. The self-extension module's
`plugin_validate` scans sources for network / subprocess / environment /
absolute-path access and flags every use the manifest did not declare.

## Visible

`registry.json` is the single source of truth (no hidden state); every
install records asset hashes; every agent action lands in an append-only audit
timeline (`.audit.jsonl`). Errors are attributed to the plugin that raised
them.

## Isolated

- Plugin code lives in `nxplugins.<id>` synthetic packages, never on
  `sys.path`; private dependencies are served only to that plugin and never
  shadow host packages.
- Dependencies install as wheels only (`--only-binary=:all:`, no build
  scripts), from PyPI and declared https indexes, with a deadline.
- Boot is staged: builtins fail fast, user plugins are isolated; two crashes
  disable a plugin, two dead boots enter safe mode.
- Routes are auth fail-closed and confined to `/api/x/<id>`; tables to
  `ext_<id>_*`; the frontend bundle is verified with SRI before import.

## Reversible

Every registry write snapshots the last-known-good file first; rollback is a
pure file operation. Bisect finds a bad plugin without reading code.

## Agent self-extension

An agent may scaffold, edit, test and register plugins for its own instance,
under: workspace-confined paths, allow-listed file types, a green test report
for the exact tree it registers, canary (agent-scope) activation first for
hooks/routes/workers/tools, a **user approval card** for every activation,
install or upgrade (10-minute timeout → rejected), a budget of three
register/activate actions per ten minutes, and hand-over to a human after two
consecutive rollbacks. The module itself is protected and cannot be modified
by an agent.
