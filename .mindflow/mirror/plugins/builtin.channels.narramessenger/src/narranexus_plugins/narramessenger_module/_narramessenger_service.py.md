---
code_file: plugins/builtin.channels.narramessenger/src/narranexus_plugins/narramessenger_module/_narramessenger_service.py
stub: false
last_verified: 2026-10-28
---

## 2026-10-28（二）— `_is_allowed_target` 改调共享包装；3xx 记日志

- PR#418 review I1：`_is_allowed_target` 不再自己判 `is_cloud_mode()`，改调
  `url_safety.assert_public_http_url_in_cloud`（部署感知只在 [[url_safety]] 一处），本层只保留
  「任何异常 → 记日志 + 返回 False」的约定；local 下包装直接放行，行为不变。
- review M6：`_get_text` 遇到 3xx（重定向一律不跟）时 `logger.warning` 记**状态码**，否则调用方把
  空串映射成「Bind token not found or expired」，上游一旦加 301 跳转，排查方向会被带偏。
  **只记状态码，绝不记 / 回显 `Location`**（上游可控内容，等于把反射面从 body 挪到 header）。
  测试 `test_refused_redirect_is_logged_by_status_without_location`。

## 2026-10-28 — bind 时的出网门（SSRF 整改）

`do_bind` 有两个服务端抓取目标都受攻击者影响：用户粘贴的 bind 链接的 host（正则只限定
`https://<host>`，`https://quota-api/...` 照样匹配），以及从该 host 返回的 setup-guide 里
**爬出来**的 homeserver URL。云端现在：

- 链接 base 在**开任何 session 之前**过共享 `assert_public_http_url`（`_is_allowed_target`，
  任何判定失败 = 拒，fail closed）；被拒回固定文案，不回显解析结果。
- 解析出的 homeserver 在 `runtime-ready` **之前**过同一个门：被拒则远端 bind 不会被完成、
  凭据也不落库。guide 里没有 homeserver 时维持原有下游处理。
- `_get_text` / `_post_json` 都 `allow_redirects=False`：host 门之后再跟 302 就等于没门。
  实测（2026-10-28）api.netmind.chat 的 setup-guide / bind-agent 端点直接回 404/500，不走
  重定向，所以这条不影响正常 bind。
- 本地 / 桌面跳过门（单可信用户，与 MCP 先例一致）。
- **步骤失败不再回显上游文本**：以前 `report-profile failed: <error>` / `runtime-ready failed: <error>`
  把 bind host 返回 JSON 里的 `error` / `message` 原样拼给用户——host 是用户选的，等于反射任意
  上游内容。现在用户文本只由 `_step_failure` 生成：`<step> failed (HTTP <code>[, <已知码>])`，
  只有 `_KNOWN_ERROR_CODES`（目前仅 `WRONG_STATE`，NarraMessenger 文档化新码时再加）原样展示，
  其余原文进日志。`_post_json` 仍返回原始 `error` 供控制流用（`WRONG_STATE` = 已过 profile 步）。

**后续项（本 PR 刻意不做）**：

- worker 侧加载已存 homeserver 时再门一次——`matrix_trigger.MatrixTrigger` 的 connect / 媒体
  下载、`_matrix_send` 的上传 / 发送 / 编辑 / 撤回、MCP 工具与 module 的发送路径都直接用存储的
  `matrix_homeserver_url`（connect 时一次 DNS 很便宜；send 路径需评估每次的开销）。
- `backend_base_url` 同样是存储后被服务端带 bearer 去打的目标：`narra_cli_client`（取
  `cred.backend_base_url` 作为 narra CLI 端点）和 `matrix_trigger` 的 authorize-event 闸
  （用它 + `bearer_token` 发请求）都不复查。bind 时它来自已过门的链接 base，但同样能被下面的
  直写路径改掉。
- bind 不是唯一写入口：`PUT` / `PATCH /api/agents/{id}/channels/{channel}/credential`
  （`backend/routes/agents/channel_credentials.py`）可以直接写 `matrix_homeserver_url` /
  `backend_base_url`，绕过 bind 门。要么在这条写路径对这两个 URL 字段过门，要么在加载时复查。

## 2026-08-19 — `runtime-ready` now carries `platform="nexus"`

NarraMessenger labels an agent's origin platform from the `runtime-ready`
call; their field **defaults to `"default"` when the key is absent**, which
is what every agent we ever bound looked like on their side (reported by
宋顺君 2026-08-19: "新绑定的 platform 显示 default，而不是 nexus"). The call
previously posted `None` as its body, so the tag was never sent.

`PLATFORM_TAG = "nexus"` is now posted as `{"platform": PLATFORM_TAG}`.

**Why runtime-ready and not report-profile** (the question that was actually
asked): report-profile is CONDITIONAL — `do_bind` skips it whenever the guide
already revealed the bearer (re-binds, resumed sessions), so a tag sent there
would silently go missing for exactly the flows that re-run most often.
`runtime-ready` is the single call every bind path makes, and it is the step
whose semantics match the label ("this agent is now usable") — which is also
what the NarraMessenger side asked for.

The value is a module constant rather than a literal so the test can assert
against the same symbol; it is deliberately NOT env-configurable — it
identifies the product, not the deployment (local, DMG and cloud all bind as
`nexus`).

Contract risk to watch: the platform silently ignores unknown body keys, so a
rename on their side would fail open (back to `default`) with no error on
our end. The test asserts the exact body of the runtime-ready POST, which
catches OUR side regressing; catching THEIRS requires checking the agent
list in NarraMessenger after a real bind.

## 2026-08-11 — `do_bind` now persists `profileId`

The `runtime-ready` response's `data.profileId` (when present) is now
written to `nexus_profile_id` on the upserted credential — previously it
was read nowhere, so the column stayed `""` for every row despite existing
in the schema since inception. This unblocks the credential manager's new
`get_by_profile_id` reverse lookup (see that file's mirror doc) for the
prewarm endpoint. `rdata.get("profileId", "") or ""` mirrors the existing
`principalId` / `roomId` handling — tolerant of the field being absent
(current prod behaviour) or explicitly `None`.

## 2026-07-02 (Commit 6) — bind flow switched to Direct Matrix

The driver now completes the bind by calling
`POST /bind-agent/runtime-ready?token=<bind_token>` (Direct Matrix) instead
of `POST /api/agent-gateway/connect` (Gateway/Polling). The setup guide's
Runtime Selection Rule is explicit that Direct Matrix is the default path
("Do not ask the creator to choose Matrix vs Gateway"). Gateway is still a
supported transport on the NarraMessenger backend for callers that hit
`/connect` directly with a stored bearer, but this driver no longer takes
that path.

Two new behaviours land with the switch:

1. `_parse_setup_guide` now extracts a fourth field — `matrix_access_token`,
   the `syt_...` string from the guide's Matrix Connection Details table.
   The Matrix HTTP API accepts ONLY this token; the guide is explicit that
   Matrix rejects the Narra bearer with `M_UNKNOWN_TOKEN`. The regex is
   conservative (`syt_` + 20+ alphanumerics) so stray `syt_short` prose
   examples don't shadow the real value.
2. `do_bind` refuses to persist a credential row when the guide reveals
   the bearer but not the Matrix access token. Reason: MatrixTrigger.connect
   raises ValueError on empty access_token, which the base treats as a
   permanent failure and disables the credential. Returning a clean error
   from `do_bind` gives the owner a better recovery hint ("re-copy the bind
   link and try again") than a silent auto-disable of a half-provisioned
   row.

The response's `roomId` is stored as `bind_room_id` on the credential — the
room where the owner will send the first test message per the guide's
Post-Bind Routing Verification step.

## Why it exists

The deterministic bind-flow driver, shared by the `narra_bind` MCP tool and the
`/api/narramessenger/bind` backend route. It replaces the original fragile path
(tell the agent to read `setup-guide.md` and self-bind) — where the agent could
pick Direct/Gateway on its own and often failed to persist the credential to our
DB. Here WE always pick Direct Matrix and always write the row.

## Design decisions

- **Single input = a pasted bind command/link.** `_parse_bind_command` extracts
  the `<token>` from a `.../<token>/setup-guide.md` URL (and derives the base URL
  from the host), or falls back to the last token-looking word for a looser
  `narra bind <token>` paste, defaulting the base to `api.netmind.chat`.
- **Flow**: GET setup-guide → if the bearer isn't revealed yet, `report-profile`
  (the agent's name+bio from the `agents` table, truncated to 30/200) → re-GET
  the guide → regex the bearer out of the `Authorization: Bearer <token>` line →
  `POST /api/agent-gateway/connect` with that bearer to activate Gateway + finish
  the bind → `upsert` the credential row.
- **Identity from the connect response, not the markdown** — `matrixUserId` /
  `principalId` / `roomId` come back from `/connect` (authoritative); only the
  bearer (+ homeserver, best-effort) are scraped from the rendered guide.
  **These three ids describe the AGENT's own Matrix identity, not the
  binder's** — `/connect` never returns who ran the bind flow. `roomId` is
  stored as `bind_room_id` and is the only trace of "where the bind
  happened"; it's what `NarramessengerTrigger._maybe_claim_owner` later
  matches against to auto-claim an owner (see that trigger's mirror doc).

## Gotchas

- **Bearer extraction is regex-over-markdown** (`_BEARER_RE`) — fragile if the
  platform changes the setup-guide layout, or if a placeholder `Bearer` appears
  before the real one. First match wins (Authentication section renders first).
- **The bind state machine may require human confirmation** (`creator_confirmed`)
  on the NarraMessenger side before the bearer is revealed. If so, `do_bind`
  returns a clear "not connected yet — confirm on NarraMessenger" error rather
  than guessing; the owner confirms there and re-pastes.
- WRONG_STATE from `report-profile` is treated as "already past the profile step"
  and ignored, so re-binding an already-connected session still works.
- **`do_bind` never writes `owner_matrix_user_id`/`owner_name`** (X2/X3 root
  cause, fixed 2026-07-02) — don't "fix" this here by trying to scrape an
  owner identity out of the setup-guide markdown; the guide describes the
  AGENT's binding, not the binder. Owner resolution happens downstream, on
  first inbound DM in the bind room (`NarramessengerTrigger._maybe_claim_owner`).
