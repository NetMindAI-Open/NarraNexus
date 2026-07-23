---
code_file: src/xyz_agent_context/artifact/_artifact_impl/heal.py
last_verified: 2026-07-21
stub: false
---

# heal.py — broken-pointer recovery strategy

## Why it exists

Under the pointer model an artifact row can outlive its on-disk entry file
(agent moved/deleted the file, legacy NULL-file_path rows, register killed
mid-flight). The raw route answers 410 for such rows; the frontend
([[useArtifactHeal.ts]]) calls heal to reconnect the pointer.

Extracted 2026-07-21 from the `agents_artifacts.py` route handler (where the
whole strategy lived inline) so it is plain, testable service logic
(`tests/artifact/test_heal.py` covers every branch) instead of HTTP-handler
body.

## The strategy (each step short-circuits)

1. **Pointer re-check** — file actually on disk → recovered, no write. Handles
   transient-410 races.
2. **Caller-picked path** (`entry_path` given, the "user picked from the
   modal" flow) — re-register onto the same artifact_id. Rejections propagate
   as `ArtifactError` so the caller sees the cause.
3. **Workspace scan by kind** (`_KIND_EXTENSIONS`, mtime desc, capped at
   `_HEAL_MAX_CANDIDATES`=10): unique match → auto-register; 0 / >1 →
   `recovered=False` + candidates for the modal.

All re-registrations go through [[registration.py]] with
`target_artifact_id` set, so kind whitelist / path confinement / size cap stay
identical to every other registration path.

## Design decisions

- Returns `HealResult` (schema model, doubles as the route's response_model) —
  the "not recovered" outcomes are data, not exceptions; only "artifact does
  not exist / not yours" raises (`ArtifactNotFound`).
- The scan does not follow symlinks; a symlink that survives the scan is still
  rejected at register time (realpath confinement).
- `application/vnd.officecli-live` maps to (.pptx, .docx, .xlsx) so heal works
  for office artifacts too (2026-07-13 behavior, carried over).
