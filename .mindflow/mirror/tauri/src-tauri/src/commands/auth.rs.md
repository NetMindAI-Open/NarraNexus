---
code_file: tauri/src-tauri/src/commands/auth.rs
last_verified: 2026-05-06
---

# auth.rs — IPC commands for Claude Code OAuth login/logout/status

Four commands exposed to the frontend:
- `trigger_claude_login`  → spawns `claude auth login`, records the child
  PID into `AppState::claude_login_pid`, then awaits the OAuth flow
- `trigger_claude_logout` → spawns `claude auth logout`, revokes local credentials
- `cancel_claude_login`   → SIGTERMs the recorded login PID; used by the
  frontend's 600s timeout (and any future explicit Cancel button) to
  abort a stuck login without holding the Child handle across awaits
- `get_claude_login_status` → `ClaudeLoginStatus { cli_installed, logged_in }`, non-blocking

## Why exists

The DMG-packaged app ships its own bundled `claude` CLI under
`Contents/Resources/resources/nodejs/node_modules/.bin/claude`, but a
Finder-launched `.app` inherits the launchd minimal PATH
(`/usr/bin:/bin:/usr/sbin:/sbin`) which never sees that bin dir. Without
an in-app login flow, users had to drop to Terminal to run
`claude auth login` — defeating the purpose of bundling the CLI.

These commands inject the bundled paths into PATH (same trick
`process_manager.rs` uses for the Python sidecars) so the frontend can
fire login/logout/status checks without ever leaving the app.

## Design decisions

**`build_child_path()` helper** — DRYs the PATH-augmentation logic that
all three commands need. Earlier inline copies drifted; the helper makes
intent obvious and removes accidental divergence.

**Login + Logout are blocking, status is not** — `claude auth login`
opens a browser and waits for the user to finish OAuth, so the Tauri
command awaits `.status()`. Status is fire-and-parse so the frontend can
poll cheaply.

**Credentials shared with user-installed CLI** — both write to
`~/.claude/.credentials.json`. There's no isolation, which is desired:
logging in via the DMG's CLI also unlocks the user's terminal CLI, and
vice versa.

## Stdio drainers (`drain_to_log`)

Both login and logout pipe stdout+stderr from the spawned `claude`
binary. A piped fd that nobody reads is a deadlock waiting to happen
— once the kernel pipe buffer (~16 KB on macOS) fills, the child
blocks on its next `write()`. This is the same failure mode that hit
`process_manager.rs` Python sidecars before its log drainer was
added (commit `5cf8c1d` — "chat hangs at agent loop step 3.2").

`drain_to_log` is a small detached `tokio::spawn` task that loops on
`BufReader::lines().next_line().await` and forwards each line to
`log::info!`. The CLI is currently quiet (a few hundred bytes over
an entire OAuth flow), so this isn't load-bearing today — but it
makes us **structurally** immune to a future Anthropic CLI release
that decides to be more verbose. Bonus: every login attempt now
leaves a debug trail in Console.app, which is invaluable when an
OAuth flow misbehaves.

The drainer task ends naturally on EOF (child closes its fd), so
there's no explicit cleanup. `kill_on_drop(true)` on the child
guarantees the fds close even if the trigger future is dropped.

## PID tracking + cancellation

`trigger_claude_login` writes the spawned child's PID into
`AppState::claude_login_pid` (an `Arc<StdMutex<Option<u32>>>`) right
after `.spawn()`, then clears it on any await resolution path
(success, child exit non-zero, IO error). `cancel_claude_login`
reads that PID under the same mutex and `libc::kill(pid, SIGTERM)`s
it. Storing only the PID (not the `Child`) sidesteps the ownership
problem of holding a not-Clone handle across await points in two
separate command futures.

`kill_on_drop(true)` on the spawn is defense-in-depth: if the Tauri
runtime drops the trigger future for any reason (app exit while a
login is mid-flight), the child is SIGKILL'd rather than leaked as
an orphan that keeps the OAuth callback port bound.

## Gotchas

- `claude auth status` JSON schema isn't formally documented and shifts
  between minor versions. The status command does only a substring match
  (`"loggedIn":true`) instead of full JSON parsing — keep it loose so
  schema bumps don't break it. Richer parsing (email, expiry) lives in
  the Python backend's `/api/providers/claude-status` endpoint.
- `which claude` is used to detect installation; that requires the
  augmented PATH too, otherwise the bundled shim won't be found.
- Both login and logout block the Tauri command future; the frontend
  must show a loading state and avoid firing concurrent calls.
- The recorded PID is cleared in trigger_claude_login's await-completion
  branch, NOT in cancel_claude_login. This avoids a stale-PID race
  where cancel runs, clears, then trigger spawns a fresh child and
  another cancel arrives racing the spawn → SIGTERMing the wrong PID.
