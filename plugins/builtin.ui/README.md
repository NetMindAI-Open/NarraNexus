# Frontend Shell (`builtin.ui`)

The frontend shell as a plugin. On the Python side this package is **manifest-only**: it declares the eighteen `ui.*` slots — one per frontend registry in `frontend/src/platform/registries` (themes, pages, sidebar, panels, settings sections, commands, channels, artifact kinds, message renderers, timeline events, tool renderers, conversation kinds and the six slot points) — so the slot tree, the catalog, `narranexus slots` and the generated docs describe the frontend extension points next to the backend ones, owned by the plugin whose shell fills them (`builtin.ts` registers under the owner id `builtin.ui`).

The contributions themselves live in TypeScript; a plugin declares its UI entries in the `frontend.ui` section of its manifest (`pages`, `panels`, `commands`, `themes`, `conversationKinds`, `messageRenderers`, `timelineEvents`, `artifactKinds`, `slots`) and the frontend loader registers them as lazy gates. `narranexus.contracts.ui` mirrors the entry shapes for documentation and validation.

`ui` is a distribution-only slot: a distribution binds its own shell; nothing binds `ui` at runtime.
