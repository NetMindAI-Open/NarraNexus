---
code_file: src/xyz_agent_context/agent_framework/remote_agent_loop_driver.py
stub: false
last_verified: 2026-07-22
---

## 2026-07-22 — 连接失败（建连 + mid-run 掉线）→ 类型化 ExecutorUnreachableError

`agent_loop` 的 `session.post` 块外包一层
`except (aiohttp.ClientConnectionError, aiohttp.ClientPayloadError)` →
`raise ExecutorUnreachableError(...) from e`（[[executor_errors.py]]）。覆盖两种：
- **建连失败**（容器没起/连不上）：ClientConnectorError / ClientOSError。
- **mid-run 掉线**（容器跑一半被杀/网络断）：ServerDisconnectedError /
  ClientPayloadError。

> PR #133 review 指出：初版只 catch `ClientConnectorError`（仅建连），但 docstring
> 声称覆盖 "mid-run drops"——文档与代码口径不一致，mid-run 掉线仍会被兜底编造回复
> 掩盖。已扩到上述基类，口径对齐。

**刻意不 catch**（照旧透传）：`_decode_event` 对 `{"error":...}` 帧抛的 RuntimeError
（executor 转发的**用户 LLM 错误**）、`_MAX_STREAM_BYTES` 自抛的 RuntimeError——两者
都是 RuntimeError 非 aiohttp ClientError；以及 `raise_for_status` 的
ClientResponseError（executor 可达但返 5xx）。上层 [[step_3_agent_loop.py]] 据类名
surface 成 `infra_transient`、skip 兜底、写审计（issue ② 根因）。

## 2026-07-15 — MCP 管道改名 `mcp_urls`/`mcp_server_urls` → `mcp_servers`

值类型从 url 字符串升级为 spec 对象 `{"url": str, "headers": {str:str}?}`，
支撑用户 MCP 自定义请求头（Authorization 等）贯穿全链路。本文件仅机械跟随
改名/类型，职责不变。

## 2026-07-09 (P0 fix) — read stream via `iter_any()`, not line iterator

The original code used `async for raw_line in resp.content`, which
calls aiohttp's `StreamReader.readuntil` under the hood. That helper
raises `LineTooLong` once its buffer crosses `_high_water = limit * 2
= 131072` bytes without seeing a newline. A single NDJSON event line
from the executor carrying a base64 image runs 150-400 KiB (Read
tool's `tool_call_output_item` embeds the image bytes *twice* — once
in `message.content` and once in `toolUseResult` metadata — so even
CLI-downsampled images blow past the 128 KiB ceiling). Result of the
old code path: every multimodal turn on the cloud died silently at
transport, the `async with` unwound the connection, executor observed
disconnect and killed the agent from outside, and the step-3 fallback
covered it up by feeding the pre-crash reasoning to a helper LLM
which invented a plausible-looking reply. The user saw the reply and
believed the agent had read the image; the agent had not (see
"多模态大文件读取事故" root-cause writeup 2026-07-08).

Fix:

1. Read with `resp.content.iter_any()` — that iterator yields whatever
   bytes the transport has, with no per-line ceiling of its own.
2. Manually accumulate an in-memory `bytearray`, split on `\n`, and
   yield each complete NDJSON event.
3. Hold an emergency ceiling `_MAX_STREAM_BYTES = 50 MiB` (aligned
   with the SDK's `max_buffer_size` in `xyz_claude_agent_sdk`) so a
   truly malformed stream still fails fast rather than eating memory.
   Experiment 3 in the writeup showed real image event lines top out
   around 365 KiB even for 3.4 MB source images, so 50 MiB is a
   generous belt-and-suspenders bound, not a tight fit.
4. Tolerate a trailing event without `\n` — the executor should end
   NDJSON cleanly but we don't want to lose the last event to a
   missing newline.

The `_FakeContent` shim in `tests/agent_runtime/test_executor_seam.py`
was updated to expose `iter_any()` (the driver no longer touches
`content` as an async iterator directly). Five new regressions
locked in on the same commit:

- `test_remote_driver_handles_event_line_over_128kib` — 200 KiB
  single event line arrives intact.
- `test_remote_driver_reassembles_line_split_across_chunks` — one
  event fragmented across four `iter_any()` yields.
- `test_remote_driver_multiple_events_in_one_chunk` — two full NDJSON
  events in one chunk both yield.
- `test_remote_driver_raises_when_line_exceeds_max_bytes` — a chunk
  without any newline past the ceiling raises fast.
- `test_remote_driver_yields_trailing_line_without_newline` — the
  no-trailing-newline case yields the last event.

Follow-ups filed separately (see writeup §六): SDK upgrade 0.1.43 →
≥0.2.113 for two independent large-output bugs; IM channel
`ErrorMessage` persistence for the zero-feedback case
(`working_source != "chat"` skips the helper-LLM fallback entirely);
post-fix multimodal e2e; and a design discussion about whether huge
payloads belong on the event stream at all.

## Why it exists

`RemoteAgentLoopDriver` — the network transport behind the step-3
`AgentLoopDriver` seam. Same `agent_loop(...)` async-generator contract
as the local claude/codex drivers, but instead of spawning the CLI
in-process it POSTs to the Executor service and streams the raw event
dicts back. This is the mirror of `HttpAgentRuntimeClient`, one layer
down (the control-plane side of binding rule #20's split).

## Selection / behaviour

- Chosen by `get_agent_loop_driver` when `AGENT_EXECUTOR_URL` is set
  (cloud orchestrator). Unset → local in-process driver, so `bash run.sh`
  and the desktop build are unchanged (binding rule #7).
- Ships the scoped provider configs in the request body
  (`executor_protocol.build_agent_loop_request` snapshots them) because
  they normally ride a ContextVar that does not survive the hop.
- Long-run safe (binding rule #14): `aiohttp` timeout `total=None`,
  `sock_read=None` — gaps between events during long tool calls must not
  abort the stream.
- Re-raises on the executor's `{"error": ...}` line so step-3's except
  path captures it exactly as a local-driver exception.

## Gotcha (burned once, 2026-06-17)

`CancellationToken.is_cancelled` is a **bool `@property`, not a method**.
The first draft called it `()` → `TypeError: 'bool' object is not
callable`, which aborted runs at the first event. Read it, do not call
it. Regression test:
`tests/agent_runtime/test_executor_seam.py::test_remote_driver_honours_cancellation_property`.
