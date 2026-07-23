---
code_file: src/xyz_agent_context/artifact/_artifact_impl/errors.py
last_verified: 2026-07-21
stub: false
---

# errors.py — structured artifact exception hierarchy

## Why it exists

Extracted from the old `artifact_runner.py` when the subsystem gained three
impl modules (registration / heal / raw_access) that all raise the same
family. Each error carries `.code` (HTTP status) so the MCP wrapper and every
route convert failures with one generic `except ArtifactError` clause — no
type-by-type branching.

| Exception | code | Trigger |
|---|---|---|
| `ArtifactError` (base) | 400 | kind not in the whitelist |
| `ArtifactTooLarge` | 413 | artifact root dir > MAX_ARTIFACT_BYTES (25 MB) |
| `ArtifactNotFound` | 404 | row missing / agent mismatch / path outside root |
| `ArtifactKindMismatch` | 400 | re-register kind ≠ existing kind |
| `ArtifactPathEscape` | 400 | entry path missing / not a file / outside workspace |
| `ArtifactContentGone` | 410 | row exists but pointed-at content is gone |

## Gotchas

- **410 vs 404 is a frontend contract**: renderers treat 410 as the self-heal
  trigger (see [[useArtifactHeal.ts]]); 404 means "no such artifact". Don't
  collapse them.
- 404 (not 403) for ownership mismatches — probing must not reveal which
  artifact_ids exist.
