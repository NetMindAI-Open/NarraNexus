---
code_file: tests/bundle/test_roundtrip.py
last_verified: 2026-05-08
stub: false
---

# test_roundtrip.py — ID Rewrite Layer 5 (PRD §8.11)

## 为什么存在

End-to-end integration test for the bundle export/import roundtrip. Layer
5 of the 5-layer defense. Asserts that:

1. `gen_new_id(kind)` always produces a string that matches `ID_KINDS[kind]`
2. `build_all_id_regex()` matches a sample of every kind
3. **Full roundtrip**: seed agent → export → preflight → confirm →
   verify all IDs were rewritten coherently, layer 2 (column-level)
   AND layer 4 (free-text) both fired, no dangling references, name
   suffix applied on clash, workspace tar got extracted to canonical path
4. **Closure correctness**: with two agents in the bundle, every
   imported row references only IDs in the new closure (no leftover
   references to original agent_ids)

## 上下游

- Test fixtures: `db_client` (isolated sqlite per test),
  `tmp_workspace_root` (overrides `settings.base_working_path` + HOME)
- SUT: `bundle.builder.build_bundle`, `bundle.importer.preflight`,
  `bundle.importer.confirm`

## What this test caught (commit history)

- Workspace text files (`.md` etc) extracted from `workspace.tar.gz`
  weren't getting their IDs rewritten by Layer 4. Fixed by adding
  `_rewrite_workspace_text_files` after `_extract_tar_safely`.

## Gotcha

- IDs in test fixtures must use `[0-9a-f]+` characters; non-hex IDs
  (e.g. "agent_orig01") won't match the production regex by design,
  so the test would falsely fail.
- `_seed_agent` derives child IDs from the agent_id suffix to keep
  multiple seeded agents from colliding on UNIQUE constraints.
