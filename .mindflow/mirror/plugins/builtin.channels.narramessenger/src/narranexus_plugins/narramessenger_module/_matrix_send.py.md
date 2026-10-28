---
code_file: plugins/builtin.channels.narramessenger/src/narranexus_plugins/narramessenger_module/_matrix_send.py
stub: false
last_verified: 2026-10-28
---

## 2026-10-28（二审）— 拒绝符号链接 workspace

`resolve_workspace_file` 原来对 workspace 根 `.resolve()`：根被换成指向别的租户 workspace 的
链接时，`narra_send_media` 能把对方的文件发出去。现在根 = realpath(base) + 字面尾段，交给
[[workspace_paths]] 的 `resolve_inside_workspace`；它的 ValueError 仍包成
`MatrixSendError("bad_path")`，单一错误类型契约不变。测试：
`test_matrix_send.py::test_resolve_refuses_a_symlinked_workspace`。

## 2026-10-28 — 保持单一错误类型

`agent_workspace_path` 自 2026-10-28 起对不安全的 id 抛 `ValueError`；这里包成
`MatrixSendError("bad_path")`，`resolve_workspace_file` 的"越界一律 MatrixSendError"契约不变。

## 2026-07-20 — doc-only: dropped a stale `narra_room_members` cross-reference

No behavior change. A header comment used to say the raw-HTTP pattern here was
"the same pattern as `narra_room_members`"; that tool was removed in the
[[_narramessenger_mcp_tools]] passthrough work (2026-07-20), so the reference was
reworded to just describe the pattern. Outbound send/media is still Matrix-native
via the agent's `matrix_access_token` (that intent is unchanged).

## 2026-07-03 (review fix) — size pre-check via `stat()` before read

PR #60 minor: `send_media_impl` now checks `target.stat().st_size` against
`max_bytes` BEFORE `read_bytes()`, so an oversized file is rejected without
being pulled fully into memory. Path-traversal confinement (`resolve_workspace_file`)
already correct.

## 2026-07-03 — `matrix_room_edit` + `matrix_room_redact` for streaming

Two new authenticated HTTP helpers alongside `matrix_room_send`:

- `matrix_room_edit(homeserver, token, room_id, original_event_id,
  new_body)` — MSC2676 `m.replace`. Sends a fresh `m.room.message`
  whose content carries an `m.relates_to` block pointing at the
  original event. Older clients see the fallback body `"* <new>"`;
  edit-aware clients (Element, NarraMessenger app) render the
  updated version in place.
- `matrix_room_redact(homeserver, token, room_id, event_id, reason)`
  — Matrix's canonical delete. Keeps the event in the timeline but
  redacts the content; clients hide the body from the conversation
  view.

Consumed by [[matrix_trigger.py]]'s
`_build_and_run_agent_streaming` state machine (progressive reply
via `m.replace` edits, silent-not-reply cleanup via redact). Kept
here rather than on the trigger so future MCP tools can reuse them
without needing a live trigger instance.

## Why it exists

Phase-3 outbound send, Matrix-native. When we added media send + unified
text reply onto Matrix (2026-07-03), the MCP tools needed to `room_send`
and upload media — but they run in the MCP server process, which has NO
live matrix-nio client (that lives in the trigger process). So these are
standalone raw-HTTP helpers against the homeserver using the agent's
`matrix_access_token`, exactly the pattern `narra_room_members` already
used. Split into their own module (not buried in the tools file) so the
trigger, the module's `send_to_agent`, and the MCP tools can all share
one implementation and one test surface.

## What's here

- `matrix_upload` — POST `/_matrix/media/v3/upload` → `mxc://`. (Upload
  has always been authenticated; only *download* moved to the
  `/_matrix/client/v1/media/download` authenticated path in MSC3916 — see
  the trigger's `_download_mxc`.)
- `matrix_room_send` — PUT `.../rooms/{room}/send/m.room.message/{txn}` →
  `event_id`. The single send primitive behind `narra_reply` delivery (via
  the trigger), `narra_send`, `narra_send_media`, and `send_to_agent`.
- `msgtype_for_mime` — image/audio/video/file, mirrors the receive side so
  send and receive agree on the coarse type.
- `resolve_workspace_file` — the security gate: confines
  `narra_send_media`'s `file_path` to the agent's own workspace
  (`agent_workspace_path`), rejecting `..` traversal / absolute escapes via
  a post-`resolve()` `relative_to` check. The agent can only ship files it
  owns.
- `send_media_impl` — orchestrates resolve → size-cap → upload → room_send.
  Never raises (returns `{"ok": false, "error"}`) so the MCP tool surfaces
  a clean result; `MatrixSendError.code` splits oversized / bad_path /
  http_error for the agent to reason about.

## Design decisions

- **Why Matrix-native, not Gateway `/chat/send`**: the transport is Matrix
  now (Commit 7 deleted the poller). Sending text via `/chat/send` while
  receiving via `/sync` was split-brain, and `/chat/send` can carry neither
  media nor (future) progressive `m.replace` streaming. Standardising on
  `room_send` unblocks both.
- **Body vs filename**: for media with a caption, `body` = caption (what
  clients show) and the real filename rides in the MSC2530 `filename`
  field; without a caption, `body` = filename.

## Related

- [[_narramessenger_mcp_tools]] — the tools that call these helpers.
- [[matrix_trigger.py]] — receive-side `_download_mxc` (the download twin);
  also delivers `narra_reply` via its own nio `room_send`.
- [[narramessenger_module]] — `send_to_agent` routes here too.
