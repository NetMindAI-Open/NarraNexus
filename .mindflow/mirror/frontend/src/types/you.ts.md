---
code_file: frontend/src/types/you.ts
last_verified: 2026-06-23
stub: false
---

# you.ts — types for the owner-scoped "You" workspace

`MyNarrative` / `MyNarrativesResponse` mirror `GET /api/me/narratives` ([[me]])
— one lived storyline per narrative across all the user's agents.
`is_special !== 'default'` marks a real storyline (vs a seeded scaffold bucket).

`MyNetworkEntity` / `MyNetworkResponse` mirror `GET /api/me/network` — one node
per entity the user's agents know, merged across agents (`known_by[]`,
`is_self` = the owner / graph centre).

`MyWorldviewLens` / `MyWorldviewResponse` mirror `GET /api/me/worldview` — one
lens per agent (`sees_you` = its view of the user, `worldview[]` = its world
observations).

Re-exported through `types/index.ts`; consumed by [[NarraMemoryTimeline]],
[[NexusNetworkGraph]], [[WorldviewLenses]] and `api` ([[api]]).
