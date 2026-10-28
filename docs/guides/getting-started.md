# Writing a NarraNexus plugin

A plugin is a directory with `narranexus-plugin.json`, an optional Python
`backend/` package, and an optional prebuilt `frontend/dist/plugin.js`.
Plugins are a **local** feature (desktop / `run.sh`); the cloud build ships the
standard plugin set.

## 1. Scaffold

```bash
narranexus plugin new acme.weather --kinds routes,settings,ui_page
cd acme.weather
```

Kinds: `routes`, `table`, `worker`, `hook`, `settings`, `tool`, `mcp_server`,
`bundle`, `skill`, `ui_page`, `ui_panel`, `theme` (see `templates/`). The
manifest lists what you provide:

```json
"provides": { "backend.routes": ["nxplugins.acme_weather:ROUTES"] }
```

`nxplugins.<id with _>` is your package name inside the host: import your own
modules relatively, never `narranexus.kernel`. Import contracts from
`narranexus.sdk`.

## 2. Test

```bash
uv run pytest tests -q
```

`narranexus.sdk.testing.PluginTestHost` boots a minimal host around your
directory: assert on `host.names("backend.routes")`, `host.tables`,
`host.hooks`, `await host.activate()`, `host.test_app()`.

## 3. Link and run

```bash
narranexus plugin link .        # registers in place (mode: link)
# restart NarraNexus; Settings → Plugins → User plugins shows it
narranexus plugin doctor        # what would load, what is blocked and why
```

Routes appear under `/api/x/acme.weather/…` (authenticated by default; use
`auth="none"` on a `RouterSpec` for an explicit public webhook). Tables must be
named `ext_acme_weather_*`. Settings resolve `NXP_ACME_WEATHER_<KEY>` env >
stored value > default; secrets are encrypted at rest.

## 4. Frontend

`@narranexus/sdk` is a real package since batch 6 (`frontend/packages/sdk`; `npm install @narranexus/sdk`): `definePlugin`, `vitePreset()` and the `HostAPI` types. At runtime the host serves its own shim for the same specifier.

`frontend/src/index.ts` exports `definePlugin({ activate(host) { … } })`; build
it with the `@narranexus/sdk` vite preset into `frontend/dist/plugin.js` and
commit the build. Declare pages/panels/commands/themes under `frontend.ui` so
the shell shows them before your code loads; activation happens on first use.

## 5. Publish

See `publishing.md`. `narranexus plugin publish-check .` runs the checklist.

## Debugging

- Settings → Plugins → User plugins shows state, warnings, isolation reason and
  the last errors per plugin.
- Two crashes disable a plugin; two dead boots enter safe mode (user plugins
  skipped); the bisect wizard finds the culprit in O(log N) restarts.
- `narranexus plugin rollback` restores the last-known-good registry.

## Slot points: adding to surfaces the shell already draws

Structural registries (`pages`, `panels`, `commands`, …) add whole surfaces. Slot points add *inside* one:

| Slot point | Where it shows | Entry shape |
|---|---|---|
| `chatHeaderActions` | the chat header's ⋯ menu | `{ label, run(ctx) }` |
| `composerExtensions` | a strip above the message input | `{ component }` (receives `agentId`) |
| `messageActions` | a message's hover strip (next to Copy) | `{ label, run({ agentId, message }) }` |
| `sidebarSections` | under the sidebar nav rows | `{ component }` |
| `agentCardBadges` | next to an agent's name in the sidebar | `{ component }` (receives that `agentId`) |
| `topBarItems` | the top bar's right cluster | `{ component }` |
| `messageRenderers` | replaces the bubble of a message `match(message)` recognises | `{ match, component }` |
| `timelineEvents` | renders a timeline event `type` the shell does not know | `{ component }` keyed by the type |
| `toolRenderers` | replaces one tool's output row in the run timeline | `{ component, accepts? }` keyed by the bare tool name (`browser_look`, not `mcp__server__browser_look`) |

Every slot entry may carry `when` — a closed grammar the host evaluates: `conversationKind:<kind>` (`chat`, `team`, or a kind a plugin registered in `conversationKinds`), `agentHas:<ModuleClass>`, `setting:<key>`; prefix `!` to negate, pass a list to AND. A typo is a registration error, never "always visible". `order` sorts within the slot (builtins use 10, 20, …).

Declare them in the manifest (`frontend.ui.slots`, `messageRenderers`, `timelineEvents`, `conversationKinds`) so the shell mounts a gate before your bundle loads; the gate activates the plugin (`onSlot:<id>`, `onRenderer:<id>`, `onTimelineEvent:<id>`) and hands over to what you register under the same id in `activate(host)`. `toolRenderers` has no manifest declaration: until your bundle registers one, the shell's generic output row is the correct rendering, so there is nothing to gate.
