# Teams (`builtin.teams`)

Agent teams: the /api/teams API and the team bulletin summary worker (feature-level plugin).

A NarraNexus builtin plugin, packaged as `narranexus-plugin-teams` (plugin platform batch 6b.3). Code: `src/narranexus_plugins/teams/`; manifest: `narranexus-plugin.json`. Contributes the `/api/teams` and `/api/marketplace/teams` routers, the `team_summary` backend worker and the `onDidStartBackend` marketplace seed hook. The team room primitives (schemas, repositories, bus helpers) stay in the platform because the message bus core uses them; this package owns the feature surface on top.
