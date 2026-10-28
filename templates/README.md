# Plugin templates

One directory per contribution kind. `narranexus plugin new <publisher>.<name> --kinds routes,table,...`
composes them into one plugin: manifest fragments deep-merge, `backend/__init__.py`
fragments concatenate, files copy with `__PLUGIN_ID__` / `__PLUGIN_PKG__` /
`__DISPLAY_NAME__` / `__TABLE_PREFIX__` substituted. Every template carries a test
that runs against `narranexus.sdk.testing.PluginTestHost`.

| kind | contributes |
|---|---|
| routes | `backend.routes` (mounted under `/api/x/<id>`) |
| table | `backend.tables` (`ext_<id>_` prefix) |
| worker | `backend.workers` (workers process) |
| hook | `backend.hooks` (`@hookimpl`) |
| settings | `backend.settings` (typed, secrets encrypted) |
| tool | `agent.capabilities.tools` + a stdio MCP server |
| mcp_server | `agent.capabilities.mcp_servers` (remote) |
| bundle | `content.bundles` (`.nxbundle`) |
| skill | `content.skills` (`SKILL.md`) |
| framework | `turn.pipeline.act.framework` (an agent-loop framework: driver + `FrameworkMeta`) |
| stage_strategy | `turn.pipeline.recall` (a Recall strategy; the same shape fits every stage slot) |
| pipeline_profile | `turn.profiles` (names the strategy each stage runs) |
| context_provider | `agent.capabilities.context_providers` (Assemble-only prompt contributions) |
| channel | `ingress.channels` + `ingress.triggers` + `agent.capabilities.modules` (an IM channel: descriptor, webhook trigger, send-tool module) |
| ui_page / ui_panel / theme | `frontend.ui` declarations + a `frontend/src/index.ts` built with `@narranexus/sdk` |

## Naming conventions

The symbols a manifest names are UPPER_SNAKE plural nouns of what they hold:
`ROUTES`, `TABLES`, `WORKERS`, `HOOKS`, `SETTINGS`, `TOOLS`, `MCP_SERVERS`,
`BUNDLES`, `SKILLS`, `RECALL_STRATEGIES`, `PROFILES`, `CONTEXT_PROVIDERS`,
`CHANNEL` / `TRIGGERS` / `MODULES` — tuples of `Contribution` (hook functions
for `HOOKS`). A ONE-arity slot is filled by a single `Contribution` named
`CONTRIBUTION` (`framework`); when one module fills several one-arity slots the
seat name prefixes it (`STOP_CONTRIBUTION`). Builtin plugins keep the same
vocabulary in their `contribution.py`, and every module filling the same
many-arity slot spells it the same way. `docs/API_POLICY.md` §8.

**Contribution ids are global within a slot.** The registry keys every
many-arity slot by the contribution's id across ALL plugins, and a second
registration of the same id is a `RegistryConflict` that isolates the later
plugin at boot. So an id is never a generic noun (`tools`, `api`, `sync`,
`team`, `schema`, `items`): the templates name every contribution after the
plugin (`__PLUGIN_PKG__`, `__PLUGIN_PKG___sync`, …), and a plugin with several
contributions in one slot suffixes them (`acme_crm_api`, `acme_crm_webhook`).
Found the hard way: the first agent-written tool plugin collided with the
hello-world sample on `tools` and was isolated (2026-09-08).
