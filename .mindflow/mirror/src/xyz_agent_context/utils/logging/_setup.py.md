---
code_file: src/xyz_agent_context/utils/logging/_setup.py
last_verified: 2026-05-22
stub: false
---

# _setup.py — single per-process logging entry point

`setup_logging(service_name)` is called once per process; idempotent per
service_name (cached in `_INITIALIZED`). Adds a stderr sink + a rotating file
sink under `$NEXUS_LOG_DIR` or `~/.narranexus/logs/<svc>/`, registers the AUDIT
level, and installs the stdlib intercept bridge.

## 2026-05-22 — resilient log dir (a bad dir must not crash the service)

Root cause of a fresh-machine "Connection failed": the log dir
(`~/.narranexus/logs/<svc>`) was created with a bare `mkdir()` and **no guard**.
On a user whose dir was unwritable (created by root on an earlier run / locked
or MDM-managed home — observed: `PermissionError [Errno 13] .../.narranexus/logs`)
the exception propagated and **killed sqlite_proxy/backend on startup** → the DB
never came up → the desktop UI could only show "Connection failed".

Fix: `_ensure_writable_log_dir(preferred, svc)` prefers **creating/repairing the
correct dir**, not working around it:
1. Probe an actual file write (`mkdir(exist_ok=True)` succeeds silently on an
   existing-but-unwritable dir, so a touch/unlink probe is required).
2. If not writable but **we own** an ancestor under `$HOME` → `_chmod_repair`
   adds u+rwx and retries. This genuinely fixes a too-tight-perms dir.
3. Only if ownership is **foreign** (root, or another Mac's uid carried over by
   Migration Assistant — which a user process cannot chown) → fall back to a
   temp dir AND print the exact `sudo chown -R <user> ~/.narranexus` fix.
4. If even that fails → `file_logging_ok=False` → `setup_logging` runs
   **stderr-only**. The service always starts.

The `probe_writable` / `chmod_repair_owned` / `chown_hint` primitives now live in
`utils/fs_safety.py` (shared with the SQLite backend so logs + DB behave
identically); `_setup` imports them under its old `_`-prefixed names so existing
monkeypatches keep working. They touch ONLY dirs we own, within `$HOME` (never
`/` or `/Users`). Warnings go to stderr — and the desktop log drainer now mirrors
all sidecar stderr to the app's own stderr, so a terminal launch shows them live.

Gotcha: don't "simplify" back to a bare `mkdir` — that reintroduces the
service-killing crash. The desktop app + `scripts/dev-local.sh` also gained a
readiness gate that surfaces such a startup death with the stderr tail + log
path, but the real fix is here: logging never takes the process down.
