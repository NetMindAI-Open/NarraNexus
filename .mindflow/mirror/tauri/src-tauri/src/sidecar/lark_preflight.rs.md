---
code_file: tauri/src-tauri/src/sidecar/lark_preflight.rs
last_verified: 2026-04-23
---

# lark_preflight.rs — lark skill pack installer, bundle-first + npx-fallback

## Intent

The `lark_skill` MCP tool resolves SKILL.md files from
`~/.agents/skills/lark-*/`. Missing → every Lark-related tool call fails
with "SKILL.md not found". The preflight's job is to guarantee those
directories exist before any Lark MCP tool is invoked.

## Design iteration (important to read before touching)

**v1 (deprecated)**: runtime-only network install via `npx skills add`.
Broken in two ways:
1. Bundled npx is a symlink into `lib/node_modules/npm/bin/npx-cli.js`
   that Tauri bundler flattens, breaking its `__dirname`-based path
   resolution. Every `npx` call failed with `MODULE_NOT_FOUND`.
2. Requires outbound network to npm registry. China users / corp
   firewalls fail unpredictably.

**v2 (current)**: bundle-first. `scripts/release/build-desktop.sh` runs
`npx skills add larksuite/cli -y -g` at BUILD time, stages the
resulting `lark-*/` directories into `resources/lark-skills/`, and
Tauri packages them into the .app. At first launch the preflight does
a local directory copy into `~/.agents/skills/`. Zero network, zero
npx dependency.

The v1 path is still present as fallback for:
- Dev mode (`cargo tauri dev`) — bundle has no lark-skills.
- Broken builds where build-desktop.sh hit its WARN path and shipped
  empty skills.

## Skip logic

`lark_skills_present()` returns true if any of:
- `~/.agents/skills/lark-shared/SKILL.md`
- `~/.claude/skills/lark-shared/SKILL.md` (symlink pattern some tooling
  creates for claude-code interop)
...exists. Either satisfies the MCP lookup order.

When copying bundled skills, existing skill dirs are NOT overwritten
— users may have manually updated a skill, or another tool (claude-code's
skills CLI directly) may have put a newer version there.

## Why recursive copy, not fs_extra crate

Keeping the Tauri binary slim. `copy_dir_recursive` is 20 lines of
std::fs and handles the two cases that matter: directories recurse,
symlinks get resolved via `canonicalize()` (matching `cp -RL` so the
user's copy is fully self-contained, no dangling symlinks if the source
is ever removed).

## Non-blocking contract

`run_preflight` must not block startup. It uses
`tauri::async_runtime::spawn` — NOT `tokio::spawn` — because setup()
fires on the Cocoa main thread where no tokio runtime handle is bound;
a bare `tokio::spawn` there panics in an FFI callback and aborts the
app at launch (observed pre-fix as SIGABRT at
`tao::app_delegate::did_finish_launching`).

## Upstream / downstream

- **Called by:** `lib.rs::run()` inside `setup()` (detached task)
- **Reads:** `Contents/Resources/resources/lark-skills/lark-*/` (bundled)
- **Writes:** `$HOME/.agents/skills/lark-*/` (per user, persists across
  app reinstalls)
- **Depends on:** `state::resolve_resource_dir`,
  `state::resolve_bundled_node_bins`, `dirs::home_dir`

## Gotchas

- If a user has an old version of a lark-* skill installed (e.g. from an
  earlier dmg or from running `npx skills add` manually), we will NOT
  upgrade it — the existing dir is preserved. To force an upgrade, user
  must `rm -rf ~/.agents/skills/lark-*` before launching the new dmg.
  This is conservative on purpose; revisit only if skill-version drift
  becomes a real support issue.
- The bundled skills list is frozen at dmg build time. If Anthropic/Lark
  ships new skills after the dmg was built, users won't see them until a
  new dmg release. Trade-off is explicit: freshness vs. offline-first.
