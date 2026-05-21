# Playbooks (Phase 2 — placeholders)

This directory hosts **task-shaped SOPs** — short, opinionated guides for
recurring engineering tasks. They are the third tier of the
`.mindflow/` doc system; the other two are:

- **Tier 1** — inline comments / docstrings / file headers
- **Tier 2** — `.mindflow/mirror/` — one md per source file with that
  file's *intent*
- **Tier 3** — `.mindflow/project/` — `references/` (deep authority)
  and **`playbooks/` (this directory)**

## Status

Most of the playbooks referenced from `CLAUDE.md` and
`CONTRIBUTING.md` are not written yet. They live in `CLAUDE.md`'s
"深度文档索引" section as a planned set:

- `onboarding.md` — Day-1 new-contributor walkthrough
- `add_new_module.md` — end-to-end "add a Module" SOP
- `add_new_database_table.md` — schema additions / migrations
- `add_new_api_endpoint.md` — FastAPI route + frontend wiring
- `add_new_frontend_page.md` — React page + state plumbing
- `debug_runtime.md` — agent_runtime pipeline debugging
- `run_tests.md` — TDD workflow
- `handle_migration.md` — DB schema changes (rule #6 — no narrowing)
- `write_nac_doc.md` — writing Tier-2 mirror md
- `work_with_worktree.md` — git worktree workflow for parallel tasks

Until each playbook is written, the fallback rule in `CLAUDE.md` applies:

1. Read `CLAUDE.md` (it covers the most common cases in summary)
2. Read the matching `.mindflow/mirror/<path>.md` for any file you're editing
3. Read the source code (file headers + docstrings are English per rule #1)
4. **Backfill the playbook** as you go — if you just figured out a
   task pattern through trial and error, please file a PR adding the
   playbook so the next contributor doesn't have to repeat your work

## Writing a new playbook

A good playbook:

- Is task-shaped (verb in the filename — "add", "debug", "handle", "run")
- Is opinionated (one recommended path, not a survey)
- Cites concrete file paths, not abstract concepts
- Ends with a checklist the AI / human can tick off
- Is short — if it's growing past 2 screens, it's probably trying to be a reference instead

Submit playbook PRs the same way as code PRs (see `CONTRIBUTING.md`).
A `playbook:` scope is welcome in your commit message:

```
feat(playbook): add add_new_module.md SOP
```
