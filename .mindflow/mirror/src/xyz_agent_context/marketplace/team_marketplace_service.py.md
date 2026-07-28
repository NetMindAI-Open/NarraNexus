---
code_file: src/xyz_agent_context/marketplace/team_marketplace_service.py
last_verified: 2026-07-21
stub: false
---

# team_marketplace_service.py

Service protocol layer — the only entry point for the Team Marketplace routes.
Encapsulates the deployment split (spec §5, decision 1): browse/detail read
the DB catalog on the registry host, proxy the cloud API on desktop; INSTALL
always runs the LOCAL importer against the LOCAL DB (fork lands in this
backend's own agents/teams), and only the "get the .nxbundle bytes" step
diverges — `resolve_bundle` reads the store directly on the registry host, or
HTTP-downloads the cloud `/download` endpoint on a desktop client. This is the
exact Local/Remote pattern from the skill marketplace, applied to bundles.

## Key methods
- `install_preflight`: resolve bytes → verify sha256 (tamper abort) →
  importer.preflight → standard preflight payload (frontend confirms via the
  existing /api/bundle/import/confirm). Bumps downloads on the registry host.
- `get_bundle_bytes`: registry-host-only, backs the /download endpoint that
  desktop clients pull through.
- `publish`: sha256 the bundle → `store_key_for(id, sha)` → put in template
  store → save catalog row. Blob lives in get_template_store() (own prefix,
  separate from skills).
