---
code_file: src/xyz_agent_context/artifact/__init__.py
last_verified: 2026-07-21
stub: false
---

# artifact/__init__.py — public surface of the artifact subsystem

## Why it exists

Artifact business logic used to live inside `common_tools_module/_common_tools_impl/`
(a Module's private impl) while three non-Module consumers — the HTTP routes,
bootstrap provisioning, and the heal endpoint — reached into it directly,
violating the "`_*_impl/` is never re-exported" rule. This package promotes
artifacts to a first-class core subsystem with one public seam.

Everything a consumer may touch is re-exported here: `ArtifactService`, the
`ArtifactError` hierarchy, `ResolvedRawFile`, and the `ALL_KINDS` /
`MAX_ARTIFACT_BYTES` constants. `_artifact_impl/` stays private.

## Upstream / Downstream

Consumers: `artifact_tool.py` (MCP tool), `backend/routes/agents_artifacts.py`,
`backend/routes/artifacts_public.py`, `bootstrap/profiles.py` (welcome
artifact). All import from this package, never from `_artifact_impl`.
