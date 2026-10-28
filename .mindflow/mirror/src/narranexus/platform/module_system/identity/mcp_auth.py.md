---
code_file: src/narranexus/platform/module_system/identity/mcp_auth.py
stub: false
last_verified: 2026-08-10
---

## Why it exists

The enforcement half of MCP caller auth (blueprint P1). `_mcp_identity.py`
reads the self-declared identity; `identity/tokens.py` proves it; this module
decides what happens: the ASGI middleware every module MCP server wears
(installed once in `module_runner._build_mcp_server` — both transports, one
choke point) plus `check_agent_ownership`, the OwnerScopedPolicy the tool
wrapper calls.

## The mode contract (`NX_MCP_AUTH_MODE`)

- **off** (default) — full no-op. This is what keeps a local `bash run.sh`
  byte-identical (iron rule #7); local enforcement is a deployment choice.
- **audit** — verify + log + record denials, never reject. The measurement
  phase: unauthenticated POSTs are AGGREGATED per **(declared caller,
  method, path, port)** and flushed once per 60s window as one WARNING line
  + one sampled `mcp_auth_tokenless` audit row — the enforce-flip decision
  reads SQL, not grep (round-2 #1; incident lesson #4/#5). The
  declared-caller key is round-3 #1: tokenless ≠ identity-less — an
  old-broker executor is exactly "bearer present, identity_token missing", so the
  self-declared user_id IS the onboarding worklist (unverified by design: it
  feeds a worklist, not an authz decision; no declaration OR a placeholder
  string → "anonymous", reusing `_mcp_identity` 的读取器与占位符语义)。
  **调用方可控 + audit 不拒 = 每个可控维度都硬上限**（round-4/5 #1）:自述值
  64 截断、path 128 截断(中间件在路由**之前**计数,POST 任意瞎编 path 也会
  先被记一笔再 404)、窗口基数 256 封顶——溢出折进**唯一常量桶**
  `_OVERFLOW_KEY`(所有维度塌缩;带 path/port 的溢出 key 自己就是新 key,
  上限只对 caller 维生效,这正是 round-5 抓的旁路)。字典真正封死在 257 条,
  总数与二值信号不丢,只有逐 key 归因饱和。method 无需上限(谓词钉死 POST),
  port 是服务端事实。flush 日志行只点名 top-20(人看信号),完整 entries 落
  审计行(SQL 查名单)。detail 落库是结构化 entries(caller/method/path/
  port/n)而非拼接串——自述值可含任意字符,SQL 拆串会错位;port 标明是哪个
  module server(一个进程 front 全部端口)。_declared_caller 与
  caller_user_id_from_request 同款两段式:显式头是占位符则**回落 bearer**,
  不让真实 usr_ 从名单上掉下来。Two documented
  approximations: handshake POSTs on /mcp are counted (label says
  "unauthenticated POST(s)"), and a final sub-window's counts can go
  unreported (flush rides the next call; the first call always flushes).
  Enforce needs no counter: its unauthenticated POSTs are individually
  401'd+logged.
- **enforce** — tokenless/invalid POSTs are 401'd at the door; cross-owner
  tool calls get an in-band error value. GETs (SSE handshake) and
  /health(z) stay open — tool calls are POSTs on both transports.
- Unknown mode value reads as **audit**, not off: a typo must surface in
  logs, not silently disable auth (warned once per distinct value —
  auth_mode() runs per request AND per tool call).

## Design decisions

- **Missing public key ⇒ whole-middleware fail-open (loud warning), checked
  BEFORE any token.** With no key provisioned the broker cannot have signed
  tokens either, so every request is tokenless and enforce would take the
  entire data plane down over a deploy misconfiguration.
- **Pure ASGI, not BaseHTTPMiddleware** — composes with the streamable-HTTP
  lifespan and never re-buffers SSE streams.
- **Every declared user_id must equal the token sub** — explicit header AND
  bearer field, checked independently in [[verify]]: consumers read the
  explicit header first, so validating only the bearer would leave the
  preferred channel forgeable (pre-push round-6 self-review). A mismatch on
  either is a forged field and invalidates the whole record.
- **The policy's proof is PER-MESSAGE, not the middleware ContextVar**
  (`verified_caller_for_tool_call`, PR #260 review #2, verified against mcp
  1.24 sources): on SSE the tool runs inside the GET /sse task, on stateful
  streamable HTTP inside the initialize-time session task — the ContextVar is
  a connection-time snapshot, while the self-declared facts are read
  per-message via `request_ctx`. Proof now comes from the same ambient
  headers (`_ambient_headers` + [[verify]]), and its verdict is FINAL even
  when it is "no proof" (falling back to the snapshot would resurrect the
  mismatch). ContextVar remains only the no-ambient-request fallback (direct
  calls/tests). Pinned by a REAL-transport integration test
  (`test_real_streamable_transport_carries_proof_to_the_tool` — TestClient
  driving `_build_mcp_server`'s actual app end-to-end).
- Header verification itself is the shared [[verify]] algorithm; this module
  keeps only the mode gate, the door check and the fail-open choice.
- `resolve_owner` goes through a 60s-TTL in-process cache
  (`_resolve_owner_cached`) so the hot tool-call path doesn't add one MySQL
  point-read per call; short TTL keeps it self-correcting, not a second
  source of truth. **Positive resolutions only** (round-2 review #2):
  `""` (unknown agent) and `None` (failed lookup — the #258 split) both fail
  open, and caching either would pin an invisible "allow" for 60s off one
  MySQL hiccup. Bounded (drop-expired then clear at 4096) so a weeks-long mcp
  process cannot leak.
- `check_agent_ownership` gates cheapest-first (agent_id shape → cloud →
  mode → THEN the stat+verify of `verified_caller_for_tool_call`) so the
  default off/local path costs nothing extra per tool call.
- `check_agent_ownership` only ever TIGHTENS a proven identity: no identity /
  local mode / unknown agent (`resolve_owner` → "") all allow, so the
  fail-open baseline of `_mcp_identity` survives intact. Denials write
  `mcp_auth_denied` to instance_executor_audit in audit AND enforce mode.

## 上下游

- 安装方: [[module_runner]] `_build_mcp_server`（wrapped Starlette 的
  middleware 参数）
- 策略调用方: [[_mcp_identity]] `_wrap_fn`（async 工具，resolved agent_id）
- 签发方: 云 = deploy 仓 broker `ensure()`；本地 = [[identity/tokens]]
  `LocalEphemeralIssuer`（step_3 dispatch 时 stamp）
