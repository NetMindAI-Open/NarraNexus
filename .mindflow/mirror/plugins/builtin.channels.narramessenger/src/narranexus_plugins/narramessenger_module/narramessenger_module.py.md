---
code_file: plugins/builtin.channels.narramessenger/src/narranexus_plugins/narramessenger_module/narramessenger_module.py
stub: false
last_verified: 2026-09-09
---


## 2026-09-09 — `_CLI_CAPABILITY`：端点归平台 + 错误保守口径

authority 段扩为 identity / tokens / **endpoint** / permissions，明确平台每次
注入 token **和**端点，禁传 `--endpoint`，忽略 runtime.md / AGENTS.md /
setup-guide 的自装 CLI、自存 token 文件、自选端点指引。新增一段（与
basic_info Product Feedback Duty 同口径）：`narra_cli` 失败时把错误码原样给用户；
`agent-token-invalid` / `no_endpoint` / 原本能用的绑定突然鉴权失败 = 平台注入的
凭据被拒，**不断言原因**、`submit_feedback(category="error",
dedup_key="narra_cli:<code>")` 上报、继续用别的方式帮用户；`official-agent-required` / `no_credential` 是设计内答复，解释即可、
不报（首版把这两个也列成上报触发，预审打回）。**绝不**把 token / access token /
凭据文件内容贴进消息（含「对比两个 token」）。
起因：prod 2026-09-09 数据星图 agent 笃定「平台缓存过期 token」并贴出 token，
真因是 [[narra_cli_client]] 端点错配。

**为什么这里必须点名 `dedup_key`**（同日随 basic_info 侧一并落地）：「报一次」现在
由 [[_basic_info_mcp_tools.py]] 的 `submit_feedback` 用 `dedup_key` 在工具侧执行，
**不传 key 就等于完全不去重**。措辞是「每个 **agent** 每个错误码一条」而不是「一条」：
闸门的键是 `(agent_id, dedup_key)` 且只在单进程内，平台级故障波及 N 个 agent 就是 N
条——写成「一条」会让下一个值班的人看到 27 条同样的 `error` 就去查一个不存在的 bug
（本仓头号历史命中形状：文案承诺代码没交付的东西）。narra_cli 恰恰是这次事故的入口、也是全仓唯一一处
带自己具体上报指引的渠道文案——平台级故障每次调用都复现，key 缺席等于闸门装了却
没接上。同理，「团队已被通知」是那次调用的**结果**（发送是 fire-and-forget，整个
部署还可能关掉 feedback），所以文案只让 agent 转述 `submit_feedback` 结果里的说法，
不许自己下断言。`tests/narramessenger_module/test_narra_failure_instructions.py`
钉住这两条。

## 2026-09-07 — 删掉 import 期的 MessageSourceRegistry 注册

顶层那段 `try: MessageSourceRegistry.register(...) except ValueError: pass` 已删；
`_extract_narramessenger_reply` 原样保留，现在由 `descriptor.py` 的 `reply_extractor_ref` 按名字指
过来、首次抽取时才解析。

原注释声称「重复注册会 hard error，这是正确的 loud-fail」，而三行之下的 `pass` 恰好把它
抵消了——两个插件抢同一个 source 名不会报错，只是「谁先 import 谁赢」。现在名字冲突由
`Registry.register` 抛 `RegistryConflict`，真的响。

行为面的净效果：`narra_reply` 这类回复工具是否被识别，不再取决于本进程有没有 import 过这个模
块，而取决于本发行版是否装了这个渠道。

## 2026-08-25 — `_BEHAVIOUR` 的 DM 指令改为服从 Communication Protocol

原文第 1 条是**无条件**的「In direct messages, every message is for you —
reply normally.」，没有任何例外条款。它与 [[channel_prompts.py]] 新增的
`### Breaking a Loop` 出现在同一个上下文窗口里，而模块级指令更「贴身」
（讲的就是这个渠道怎么用工具），模型仲裁时很可能压过通用协议——出口就在
8/14 事故所在的这条链路上被自家 prompt 抵消。

改成一句**引用**：「reply normally, subject to **Breaking a Loop** in the
Communication Protocol above」。

**只引用、不复制正文**。把 loop 规则抄进模块 prompt 就是两份文案，下次改
协议必漂移——本仓已经为「静态 bus 规则与 team 房间 prompt 互相矛盾」付过
一次学费。`TestModulePromptsDoNotContradictTheProtocol` 两侧都钉：引用必须
在、规则正文必须**不**在。

Slack / Discord 的同类措辞是「every **relevant** message」，本身留了余地，
未改动。

## 2026-08-06 — auto review 收口（PR #247 两轮意见）

定位说明（review #12）：语音轮表达面保留 narra_reply 是刻意的——它是 trigger 唯一的兜底投递工具（bridge 无产出时 legacy finalize 靠它），「ONLY way = speak」的绝对断言只针对 plain prose；改这段的人不要顺手把 narra_reply 从语音轮表达面摘掉。

## 2026-08-06 — voice fast mode: RTC 检测 + voice register + speak

expressive_tools：extra_data.rtc_voice 存在时 speak 全限定名置于首位（首位=默认回复工具）；speak 不进 reply_tool_names——普通 turn 无 voice bridge，列出即声明死工具。all_tool_names 增 speak（drift guard 同步）。

## 2026-08-04 — 过滤精确化 + `_is_nm_turn` 去重(review)

托管声明过滤从子串改 `endswith("__narra_reply")`(未来 narra_reply_*
兄弟工具不再连坐);来源判定抽 `_is_nm_turn`,contribute_instructions 与
expressive_tools 共用。
## 2026-08-03 — `expressive_tools` 增加可选 ctx_data(按来源声明)

回复面声明可按 turn 来源变化——声明面绝不能列出本回合无法投递的死工具
(那是喂给模型的错误信息,弱模型遇声明/指令冲突时常以"写成文字"收场)。
首个消费者:narramessenger 托管回合剔除 trigger 捕获式的 narra_reply,
只声明 narra_send。无 ctx 调用方(测试/旧路径)行为不变。

## 2026-08-03 — 托管来源回合的回复指令切 `narra_send`

`build_extra_data` 透传 `managed_ingress`(来自 openai_compat 分流的
trigger_extra_data);`contribute_instructions` 对 NARRAMESSENGER 来源 + managed
的回合渲染 `_managed_reply_action_block`——指令 agent 用
`narra_send(room_id=…, text=…)` 直发并明确禁用 `narra_reply`(它是
trigger 捕获式标记,托管模式 MatrixTrigger 不跑,调了等于静默丢失,
7-30 P8 的一半根因)。原生来源回合与 proactive 分支不变。回复分类侧
无需改动:MessageSource 注册的提取器本就覆盖 narra_send。

## 2026-07-31 — 回复契约:投递面由平台声明(expressive seam)

`reply_tool_names = ("narra_reply", "narra_send")`(NexusPower 投递面声明;
门控随 is_bound)。

## 2026-07-24 — setup residency (B++): unbound → one-liner + tool suppression

Declares `all_tool_names` + `setup_tool_names = {narra_bind}` per the
[[channel_module_base]] setup-residency contract. The `contribute_instructions`
unbound branch now returns `unbound_setup_line()` instead of the full
onboarding walkthrough (bound-but-info-missing returns ""); the walkthrough is
served on demand by empty-arg `narra_bind` (see [[_narramessenger_mcp_tools]]).
While unbound, every non-setup tool's schema is stripped from the model context.

## 2026-07-21 — `_CLI_CAPABILITY` closing line clarified (writes ≠ read-only)

The closing "Sending stays on narra_reply/narra_send…" line was reinforcing an
agent's wrong belief that `narra_cli` is read-only (see [[_narramessenger_mcp_tools]]
2026-07-21). Reworded: only CHAT messages go elsewhere; explore *writes* and
everything else ARE `narra_cli` — "don't refuse a write as read-only, call the
tool and try".

## 2026-07-20 — `_CLI_CAPABILITY` instruction block

``contribute_instructions`` injects a ``_CLI_CAPABILITY`` block between ``_BEHAVIOUR``
and ``_IRON_RULES``. Companion to the [[_narramessenger_mcp_tools]] passthrough
work (2026-07-20). Reply/proactive/media guidance unchanged.

Reframed later the same day (**"### Operating NarraMessenger — narra_cli +
narra_guide"**, was "### Reading room context"): an agent asked about "publish"
and couldn't tell whether it was a NarraNexus / NarraMessenger / other feature.
Fixes:
- the block now opens with what NarraMessenger *is* (the IM network) and frames
  ``narra_cli`` as "how you operate it", not just "read room context";
- gives a **capability overview** incl. **explore/publish** (official-agents-only;
  server returns ``official-agent-required``) with an explicit
  ``"publish a post" = narra_cli("explore publish ...")`` mapping — the missing
  signal that caused the confusion;
- points to ``narra_guide()`` / ``<domain> --help`` for exact flags;
- adds an **authority-on-conflicts** clause: the NarraNexus platform is SSOT for
  identity / tokens / permissions (never pass ``--token*``; ignore runtime.md
  token/endpoint self-management guidance); ``narra_guide`` / runtime.md is only
  for narra-cli command *syntax*, not platform policy.

## 2026-07-03 — Matrix-native send + stale-prompt sweep

Part of the outbound send unification (see [[_matrix_send]] / [[_narramessenger_mcp_tools]]).

- **`send_to_agent`** (ChannelSenderRegistry) repointed from `/chat/send` to
  Matrix `room_send`. This module no longer imports `NarramessengerClient` for
  sending (only bind/status uses it now).
- **MessageSourceRegistry handler**: the memory extractor now recognises
  `narra_reply` / `narra_send` (both carry text in the `text` arg) and DROPS
  `send_message_to_user_directly` — that generic tool is the OWNER channel, not
  a room reply, so logging it as a NarraMessenger reply was wrong.
- **Prompt sweep (behaviour-lied fixes, cf. the group-visibility fix below)**:
  `_BEHAVIOUR` point 3 said "images/files… you cannot open them" — false since
  the Phase-3 receive landed; now it tells the agent files are downloaded to the
  workspace + `Read` them, and `narra_send_media` to send. `_reply_action_block`
  dropped the dead `invocation_id` (Matrix has none — the trigger knows the
  room); `_PROACTIVE_ACTION` reworded. `current_invocation_id` removed from
  `build_extra_data` (write-only after the reply-block change).

## 2026-07-02 — `_BEHAVIOUR` group-visibility line rewritten

The old rule 2 read "In group rooms you are only invoked when
@-mentioned" and stopped there. That was accurate under Narra-strict
policy (group non-@ events were denied and never reached memory), but
after the `SILENT_BYPASS_AUTHORIZE` override (see
[[matrix_trigger.py]] owner override note), the agent's chat_history
DOES contain silent-ingested group messages — the LLM was hallucinating
"I can't see non-@ messages" because the prompt told it so.

Rewritten rule 2 now says explicitly: "You SEE every message (silently
ingested into your conversation memory even when you weren't summoned),
but you only REPLY when directly @-mentioned." It also names the
`silent=true` metadata marker so the LLM can distinguish
silently-ingested rows from directly-addressed turns when the
distinction matters.

Verified live: after this change, `agent_62cf67080ad4` on a group
non-@ ingest (already in chat_history as `silent=True`) correctly
answers "yes, I saw that message" when @-mentioned about it. The
memory infrastructure was fine; only the prompt lied.

## 2026-07-03 — handler registers `dedicated_trigger=True`

MessageBusTrigger derives its do-not-redispatch channel prefixes from this
flag (see message_source_handler.py.md, 2026-07-03).

## Why it exists

The agent-facing surface of the NarraMessenger channel (`ChannelModuleBase`).
Owns the sender (`send_to_agent` → `/chat/send`, registered in
`ChannelSenderRegistry`), the `narra_reply`/`narra_send`/`narra_bind`/
`narra_status` MCP tools, the per-turn `contribute_instructions` (system-prompt
behaviour), and `build_extra_data` (trust signal + threaded ids). Mirrors
`telegram_module.py`.

## 2026-06-18 — prompt refactor + reply/send split

- Prompt text extracted to module-level constants (`_SETUP_INSTRUCTION`,
  `_BEHAVIOUR`, `_IRON_RULES`, `_PROACTIVE_ACTION`) — lark/telegram convention;
  `contribute_instructions` only assembles named sections (`_trust_block` +
  `_reply_action_block` are the dynamic, id-interpolated pieces).
- `contribute_instructions` renders by `working_source`: REPLY mode (ws ==
  NARRAMESSENGER) shows an **identity block** with sender/room_id/**invocation_id**
  and tells the agent to call `narra_reply(invocation_id, text)`; otherwise the
  proactive `narra_send(room_id, text)` block.
- `build_extra_data` threads `current_invocation_id` (parsed from
  `trigger_id = "narramessenger_<invocation_id>"`) + `current_room_id` into
  `narramessenger_info`, so the agent can copy the invocation_id into
  `narra_reply` (same as it copies room_id). This is what fixes the timeout.

## Design decisions

- **`send_to_agent` and `narra_send` both go through `/chat/send`** (bearer,
  `txn_id`=uuid4, no reply deadline). The agent replies by calling `narra_send`
  with the inbound `room_id`; the registry path serves composite/proactive
  sends.
- **`MessageSourceRegistry` handler** (`name="narramessenger"`, reply tools
  `narra_send` / `notify_owner`) so ChatModule captures
  NarraMessenger replies into chat history instead of logging "Background
  activity". Registered at import, idempotent.
- **`contribute_instructions` is short (~telegram-sized), NOT lark's 600 lines.**
  Identity + how-to-reply + DM/group behaviour + owner trust block + an
  explicit **output-hygiene iron rule**: never emit identity/trust/instruction
  text as a `narra_send` reply. This directly targets a real bug observed on a
  cloud responder ("I am X's agent. X has full access to my account.").
- **Trust signal**: `owner_matrix_user_id == channel_tag.sender_id` →
  `is_owner_interacting`. Same model as Slack/Telegram/Lark.
- **`owner_matrix_user_id` is populated by the trigger, not this module**
  (2026-07-02, X2/X3 fix). This module only reads it; it never writes it.
  `do_bind` can't learn the binder's identity (see `_narramessenger_service.py.md`),
  so `NarramessengerTrigger._maybe_claim_owner` claims the first sender in
  the bind room as owner on the first inbound message and persists it via
  `NarramessengerCredentialManager.update_owner`. Before this fix
  `owner_matrix_user_id` was permanently empty post-bind, so
  `is_owner_interacting` was always `False` and `_trust_block` always
  rendered "No owner is registered" — the agent could never recognize its
  own owner.

## Upstream / downstream

- **Upstream**: `ChannelModuleBase` → `XYZBaseModule`.
- **Registers**: sender (via base `__init__`), MessageSourceRegistry handler.
- **Calls**: `NarramessengerClient.chat_send`, `NarramessengerCredentialManager`.
- **Fed by**: `NarramessengerTrigger._maybe_claim_owner` (owner identity —
  see that trigger's mirror doc for the write side of the X2/X3 fix).
- **MCP**: port 7833, server name `narramessenger_module`.

## Gotchas

- `get_config` is a `@staticmethod` (like all channel modules); pyright flags
  the override as incompatible with the base instance method — this is an
  accepted codebase-wide pattern (identical on telegram), not a bug.
- If the v1 reply policy changes from `/chat/send` to `/reply`, update both
  `send_to_agent` and the `narra_send` tool, and revisit `extract_output` in
  the trigger.

## 2026-08-18 — 工具改名映射（新增条目；上面带日期的历史条目一律不改写）

本文件上方带日期的条目里出现的是**当时**的工具名，故意保持原样 —— 镜像的价值就在于它记的是
那一天发生了什么，在带日期的条目里改名会让「什么时候变的、从什么变的」不可考。第三轮预审在
23 个文件里查出 68 处这种改写，已全部还原。

现行名字与旧名字的对应：

| 旧 | 新 |
|---|---|
| `send_message_to_user_directly` | `reply_owner`（回答刚说话的 owner）/ `notify_owner`（未被问就主动告知） |
| `bus_send_message` | `message_team` |
| `bus_send_to_agent` | `message_agent` |
| `bus_get_messages` | `read_history`（且改为按会话把手取，不再收 channel_id） |
| `bus_create_channel` | `create_team` |
| `bus_share_to_team` | `team_share_file` |
| `work_add_item` / `work_complete_item` / `work_update_status` … | `team_work_add` / `team_work_complete` / `team_work_update_status` … |
| `ChannelInboxWriter` | `InboxRecorder`（且改写自己的两张表，不再写 bus 表） |

规范解释见 [[chat_module.py]] 与 [[message_source_handler.py]] 的 2026-08-18 条目。

## 2026-09-04 · no per-module port (batch 5a)

The MCP server URL comes from `mcp_server_url("<server_name>")` (the single MCP host + `/mcp/<server_name>/sse`); the module-level port constant / `self.port` and the factory's `port` parameter are gone.
