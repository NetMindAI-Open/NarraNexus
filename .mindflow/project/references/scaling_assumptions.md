---
title: Scaling Assumptions & Multi-Worker Checklist
last_verified: 2026-05-08
status: live
---

# Scaling Assumptions & Multi-Worker Checklist

> **Why this doc exists**
>
> Backend is currently deployed as **uvicorn single worker, single container**
> (see `stacks/narranexus-app/compose.yml` `backend.command:`). A handful of
> features were intentionally written assuming this. If anyone ever flips
> `--workers N` or scales to multiple pods/replicas, those features will
> regress in subtle ways. **This is the single place that catalogs all such
> assumptions** so the migration is mechanical instead of archaeological.

---

## TL;DR — what's safe today, what'll break later

| Assumption | Safe under single worker | Will break when |
|---|---|---|
| Some module-level Python state can live in process memory | ✓ | `--workers > 1`, or multi-pod deploy |
| Local filesystem (`~/.nexusagent/...`) is the same disk for every request | ✓ on `bash run.sh`. ✓ in docker compose **iff** the path is on a shared named volume | Multi-pod **without** a shared persistent volume (e.g., k8s with ephemeral storage) |
| WebSocket connection state is owned by whichever process holds the socket | ✓ | Multi-worker with non-sticky LB; or any pod restart |

If you ever hit one of those triggers, search this repo for the literal
string `# SINGLE-WORKER ASSUMPTION:` — every code site that depends on the
above is annotated with that comment so you can grep for it.

---

## 1. Bundle preflight + import (subproject 2)

### What's there now

`POST /api/bundle/import/preflight` extracts a `.nxbundle` zip into
`~/.nexusagent/bundle_preflight/<token>/` on disk and writes a row into
the `bundle_preflight_sessions` table mapping `token → user_id, work_dir,
manifest_json, created_at`. `POST /api/bundle/import/confirm` reads the row
back and reads files from `work_dir`.

### Why it's safe under single worker on a single host

- The DB row makes the token **survive process restarts** (this is fixed already).
- The work_dir path (`~/.nexusagent/bundle_preflight/...`) lives on the host
  filesystem; same process = same view of the disk.
- TTL cleanup (6h) inline at the top of every preflight call.

### What breaks under multi-worker / multi-pod

- **Worker A handles preflight, worker B handles confirm**. The DB row is
  shared (DB-backed), so the token validates. **But `work_dir` is a
  filesystem path**: if worker B is in a different pod with a different
  filesystem, the path doesn't exist → confirm fails with "preflight working
  dir missing — please re-upload the bundle".
- The `_extract_tar_safely`, `extract_zip_safely` helpers call `tar.extractall`
  / per-file copy directly into the host fs. Same caveat.

### What to do when scaling

1. **Mount a shared volume** for `~/.nexusagent/bundle_preflight/` (NFS, EFS,
   or a CSI volume that supports `ReadWriteMany`). All replicas see the same
   files.
2. *Or* upload the extracted tree to S3 / object store under a key namespaced
   by token, rewrite `work_dir` references to operate on the bucket.

Both are 1-2 day jobs. Bumping a single env var (`BUNDLE_PREFLIGHT_DIR`)
already exists in spirit (the path is hardcoded under `Path.home()`); make
it configurable when you do this.

### Code sites to grep

- `src/xyz_agent_context/bundle/importer.py` — `preflight()` `confirm()`
- `src/xyz_agent_context/bundle/security.py` — `extract_zip_safely`
- `backend/routes/bundle.py` — upload handler

---

## 2. Skill archives (subproject 2)

### What's there now

Auto-archive after `POST /api/skills/install` writes the original zip (or
GitHub tarball) to `~/.nexusagent/skill_archives/{user_id}/<name>.zip` on
the local disk and records a row in `skill_archives` (path + sha256 + source
metadata). Bundle export reads those archives by `archive_path`. The 4 skill
backup MCP tools (`skill_backup_from_github` etc.) write to the same
location.

### Why it's safe under single worker

- All install / export / backup code paths run in the same process, so the
  fs and DB views are consistent.

### What breaks under multi-worker / multi-pod

- `archive_path` in DB is an absolute filesystem path. Multi-pod = pod A
  installs a skill, pod B exports a bundle that references the absolute
  path → file not found.
- **NB**: this exact path is *also* used by the existing skill-install
  flow (pre-existing code); not new. But subproject 2 widened the blast
  radius (export now depends on the archive being there).

### What to do when scaling

1. Move skill archives to **object storage**. Write `archive_path` as
   `s3://bucket/skill_archives/{user_id}/{skill_name}_{sha8}.zip` instead
   of local fs path.
2. Update `bundle.builder` and `bundle.importer` to fetch via the object
   store URL when `archive_path` starts with `s3://` / `gs://` / etc.
3. Or mount a shared volume the same way as §1.

### Code sites to grep

- `src/xyz_agent_context/bundle/skill_backup.py` — `_user_archive_dir`,
  `archive_github_tarball`, `archive_md_only`, `archive_local_zip`
- `src/xyz_agent_context/bundle/builder.py` — full_copy / zip method
  reads `archive_path` directly from DB row
- `backend/routes/skills.py` — `install_skill` route's auto-archive call
- `backend/routes/bundle.py` — `/api/bundle/skills/archives/upload`

---

## 3. Agent workspaces (pre-existing, but bundle export reads them)

### What's there now

Each agent has a workspace dir at `~/.nexusagent/workspaces/{agent_id}_user_{user_id}/`
holding skill installs, study artifacts, agent-produced files. Bundle export
tar.gz's this directory; bundle import un-tar's into the same path on the
recipient's machine.

### Why it's safe under single worker

- Same process → same fs.

### What breaks under multi-worker / multi-pod

- An agent run on pod A produces files under `workspaces/`. The user clicks
  Export Bundle, request lands on pod B, pod B's `workspaces/` directory is
  empty for that agent → bundle ships an empty `workspace.tar.gz`.
- Docker compose **already handles this** for the cloud single-host
  deployment by mounting `workspaces:/opt/narranexus/workspaces` (named
  volume) into every Python service. Multi-pod deployments must do the
  same with a `ReadWriteMany` volume or migrate to object storage.

### What to do when scaling

Same options as §1/§2 — shared volume or object storage. The compose stack
already shows the volume pattern; k8s manifests need to mirror it.

### Code sites to grep

- `src/xyz_agent_context/bundle/builder.py` — `_pack_workspace_sync` (path
  candidates: `_user_<user_id>` and `_<user_id>`)
- `src/xyz_agent_context/bundle/importer.py` — workspace tar extract target
- `src/xyz_agent_context/module/skill_module/skill_module.py` — pre-existing
  workspace path resolution

---

## 4. Database client per event loop (pre-existing, called out for completeness)

### What's there now

`db_factory.get_db_client()` returns one `AsyncDatabaseClient` per running
asyncio event loop, keyed by `id(loop)`. Each event loop builds its own
aiomysql / sqlite pool.

### Why it's safe under multi-worker

- This one is **already multi-worker friendly**. Each worker has its own
  loop, gets its own pool, points at the same DB. No shared state.

### Caveat

- `SQLITE_PROXY_URL` makes SQLite usable from many processes (the proxy
  serializes writes). If you stay on SQLite when scaling, set this env var.
- For real scale, switch to MySQL/Postgres and let the DB do its job.

---

## 5. WebSocket connections (pre-existing)

### What's there now

`backend/routes/websocket.py` keeps an in-memory dict of active connections
per `(agent_id, user_id)` so chat messages can be pushed back. Single
worker = single dict.

### What breaks under multi-worker

- User connects to worker A, user's agent does something on worker B (e.g.
  a job completion handler), worker B can't push to the WS because it
  doesn't own the socket.

### What to do when scaling

- Put **sticky sessions** at the load balancer (route same user to same
  worker), OR
- Use Redis pub/sub to fan out events across workers and let any worker
  push to its own owned sockets.

### Code sites to grep

- `backend/routes/websocket.py` — connection registry

---

## 6. Module poller / job trigger / message bus trigger (pre-existing)

### What's there now

These are separate processes (see `compose.yml` services `poller`, `jobs`,
`bus`) each maintaining their own scan loop against the DB. They're already
"multi-process-friendly" — they coordinate through DB state, not memory.

### What breaks if you start two of each

- `ModulePoller` would race-condition on which one claims a completed
  instance. Currently it does an UPDATE...WHERE callback_processed=0
  pattern that's idempotent at the row level but not at the trigger level
  (would fire dependency chains twice). Needs `SELECT ... FOR UPDATE` /
  advisory locks before scaling these horizontally.

This is **out of scope for subproject 1 / 2** but worth knowing.

---

## How to verify after scaling

When you ever bump workers / pods, run this checklist:

1. ☐ `bash run.sh` still works locally as a baseline
2. ☐ Cloud `docker compose up -d` with the new config; `/health` returns 200
3. ☐ Manual: log in two browser windows from different IPs (forces LB to
   distribute), confirm both can chat
4. ☐ Manual: export a bundle in window A, import it in window B 30s later
   — should succeed (this exercises preflight cross-worker behavior)
5. ☐ Manual: install a skill in window A, in window B click Export bundle
   and confirm the skill shows up with the `zip` install method
6. ☐ Server logs: grep for `preflight working dir missing` over the next
   week — any hit means §1 wasn't done right

---

## Appendix: how this doc is maintained

- **Owner**: whoever's on the next "scale beyond single-worker" task.
- **Update trigger**: any new feature that adds module-level mutable state
  or local-fs writes must add an entry to the relevant section.
- **Code-side annotation**: when you add code with a single-worker
  assumption, leave a comment `# SINGLE-WORKER ASSUMPTION: <one-line
  reason>` next to the offending line. Grep this string in CI as a
  reminder lights-on.
