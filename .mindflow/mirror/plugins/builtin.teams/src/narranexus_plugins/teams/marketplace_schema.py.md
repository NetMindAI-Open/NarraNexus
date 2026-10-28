---
code_file: plugins/builtin.teams/src/narranexus_plugins/teams/marketplace_schema.py
last_verified: 2026-09-04
stub: false
---

# team_marketplace_schema.py

`TeamTemplate` — one catalog INDEX row for the Team Marketplace: presentation
metadata (name/description/categories/agent_count/thumbnail) + a pointer
(`store_key` + `bundle_sha256`) to a `.nxbundle` in OUR artifact store.
Diverges from the unmerged ee1db871 `MarketplaceTemplate` on ONE field:
`bundle_url` (external narra.nexus URL) → `store_key` (our store), because we
self-host blobs (S3/local, separate from skills) instead of pointing at
narra.nexus. `agent_count` is a UI badge only — a "team template" is just a
multi-agent bundle (team_id set); single-agent bundles use the identical
format. Timestamps are Optional[datetime] (SQLite returns datetime objects).

Batch 6b.3: moved from `platform/schema/team_marketplace_schema.py`.
