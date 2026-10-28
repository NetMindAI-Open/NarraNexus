---
code_file: src/narranexus/platform/channel/channel_module_base.py
stub: false
last_verified: 2026-09-07
---

## 2026-09-07 — legacy 凭据清理移到提前返回之前

`cleanup_for_agent` 里 `LEGACY_TABLES` 的清理原来排在「该 agent 在通用
`channel_credentials` 里没有行 → 直接 return」**之后**，于是它恰好跳过了它被写出来要解决
的那个场景：legacy 行从来没被拷进通用表的 agent。而拷贝
（`credential_legacy.copy_legacy_tables`）走 `descriptor_for`，对任何本发行版没装的渠道
都会 `UnknownChannel` 并被 warning 吞掉——也就是说，越是「插件被排除」的部署，残留越确定
发生。

现在清理在 return 之前跑，且改为调 `credential_legacy.purge_legacy_for_agent(db,
agent_id, channel=self.channel_name)`：它只按 `agent_id` 定位，不需要通用行、不需要描述
符。`channel=` 让这一步保持**每渠道**语义，`stats` 仍然只可能多出
`channel_credentials.<channel>` 这一个键（M10 定下的「一个渠道一条」不变，purge 的返回值
故意不并进 stats，否则会重复计数）。

真正兜底的那一遍在 `backend/routes/auth.py` 的删除 agent 路由里（见该文件），因为这里的
遍历只走 module_registry 里注册着的 `ChannelModuleBase` 子类——被发行版排除的渠道根本没
有模块可走。

## 2026-08-19 — plain-text（巡查）回合不声明任何回复工具

`expressive_tools` 在 `BUS_PLAIN_TEXT_TURN_EXTRA_KEY` 为真时返回 `[]`：巡查回合无任何回复工具适用,声明会让回复提醒命名它、与「写纯文本别调工具」互斥。覆盖全部 6 个渠道模块。只撤声明,schema 不动。同类见 [[chat_module]]。

## 2026-08-04 — claims_source：channel_name 即来源名

WorkingSource 的 IM 值复用 channel_name（"wechat"/"lark"/...），故基类
统一实现，比较走 [[base]] 的 working_source_matches（(str, Enum) 一个
== 两形态通吃，review 修正后四处覆写共用同一谓词）。配合
[[context_runtime]] origin-first 排序，WeChat 触发轮的默认回复工具是
wechat_send 而非 owner-chat 工具。

## 2026-08-03 — `expressive_tools` 增加可选 ctx_data(按来源声明)

回复面声明可按 turn 来源变化——声明面绝不能列出本回合无法投递的死工具
(那是喂给模型的错误信息,弱模型遇声明/指令冲突时常以"写成文字"收场)。
首个消费者:narramessenger 托管回合剔除 trigger 捕获式的 narra_reply,
只声明 narra_send。无 ctx 调用方(测试/旧路径)行为不变。

## 2026-07-31 — 回复契约:投递面由平台声明(expressive seam)

新类属性 `reply_tool_names`(短名,⊆ all_tool_names,cross-channel 测试钉住);
`expressive_tools()`:bound → 全限定 reply 工具,unbound → 空(与
setup-residency 同门控——未绑定时这些 schema 本来就被压掉)。测试:
tests/channel/test_setup_residency.py 第 6 节。

## 2026-07-24 — setup residency (B++): unbound channels go quiet

Unbound channel modules no longer inject their ~8K onboarding walkthroughs nor
expose their tools' schemas. New contract on this base: `all_tool_names` /
`setup_tool_names` class attrs each subclass declares; `is_bound()` — memoized
per instance, FAIL-OPEN on errors (wrongly gating a bound channel is
user-visible loss; wrongly keeping an unbound one only costs tokens);
`disallowed_tools()` — unbound → every non-setup tool as
`mcp__<server>__<tool>` (overrides the generic surface in [[base.py]]); and
`unbound_setup_line()` — the one-liner subclasses return while unbound.
Drift guard: tests/channel/test_setup_residency.py. Plan: W2 B++ in
reference/self_notebook/plans/2026-07-23-token-consumption-optimization.plan.md.

## Why it exists

Phase 2 of the IM channel abstraction. Captures the structural
boilerplate every IM Module needs (sender registry self-registration,
``gather`` template, MCP server creation glue) without
constraining each channel's product-surface decisions (LLM
instructions, MCP tool count/shape, credential schema).

Pairs with Phase 1's ``ChannelTriggerBase`` to give Slack/Telegram in
Phases 3/4 a complete "fill in the blanks" experience for adding a new
IM integration: subclass two bases + write platform-specific content.

## Design decisions

- **Mechanism only, no content.** ``contribute_instructions`` is abstract —
  Lark writes 600 lines (three-click flow + iron rules + identity
  guide), Telegram might write 150 lines (no admin approval, no
  identity model). Same with MCP tools and credential schema. The
  base does not impose a shape.

- **Sender registers exactly once per channel.** Class-level
  ``_sender_registered_for_channel`` dict guards against double
  registration when multiple subclass instances are constructed in
  the same process (one per agent).

- **``build_extra_data(cred, ctx_data)``, not just ``cred``.** Lark's
  ``is_owner_interacting`` trust signal depends on the current
  channel_tag in ``ctx_data.extra_data``, derived per turn. The
  signature carries ``ctx_data`` so subclasses with similar
  per-turn-derived fields work without contortions.

- **``after_turn`` filter uses both enum and string
  comparison.** Python 3.11+ changed ``str(enum_member)`` to return
  the qualified name; ``str(WorkingSource.LARK) == "lark"`` is False.
  The base uses direct ``ws == self.working_source or ws == self.working_source.value``
  to handle both serialization shapes (``WorkingSource`` inherits
  ``(str, Enum)`` so member equality with the string value works).

- **``create_mcp_server`` returns None on import error.** A stripped
  image without ``fastmcp`` installed should still boot — the rest of
  the channel runs without agent-callable tools.

- **Get-then-insert idempotency** (inherited from ChannelInboxWriter
  pattern): ``gather`` swallows credential-load
  exceptions and logs a warning; the agent loop's ability to gather
  context for OTHER modules must not break because Lark's DB hiccupped.

## Upstream / downstream

- **Upstream**: ``LarkModule`` (Phase 2). Phase 3 ``SlackModule`` and
  Phase 4 ``TelegramModule`` will both subclass.
- **Downstream**:
  - ``XYZBaseModule`` — superclass; provides ``agent_id``, ``db``,
    ``mcp_host``, ``get_mcp_db_client``.
  - ``ChannelSenderRegistry`` — registry the base self-registers
    into.
  - ``mcp.server.fastmcp.FastMCP`` — lazy-imported only when
    ``create_mcp_server`` is called.

## Gotchas

- Subclass MUST set ``channel_name`` AND ``ctx_data_key``. The
  ``__init__`` raises ``ValueError`` if either is empty — caught at
  module instantiation, not at runtime, so it surfaces via the
  framework's startup path.
- ``_sender_registered_for_channel`` is class-level on
  ``ChannelModuleBase``, not on each subclass. This means multiple
  subclasses (e.g. LarkModule + future SlackModule) share the same
  guard dict but use different keys (``channel_name``), so they don't
  interfere.
- ``XYZBaseModule.get_config`` is abstract. The base does NOT provide
  a default — each subclass writes its own (priority, description,
  module_type vary per channel).

## 2026-08-18 — `disallowed_tools(ctx_data)` 签名同步

跟随 [[base.py]] 2026-08-18 的接缝修复：压制 hook 改读本轮自己的 ctx，不再依赖声明 hook
留下的实例状态（`_last_ctx` 已删）。收集环先压制后声明，旧写法在全新实例上必然误判。

## 2026-09-04 · cleanup deletes the generic binding (batch 4d.2)

`cleanup_for_agent` unbinds the agent's row in `channel_credentials` through `GenericCredentialStore` (stat key `channel_credentials`); the retired per-channel table name (`_credential_table_name`) is gone. Subclasses that need the decoded credential during cleanup (lark: workspace path) read it through their manager first.

## 2026-09-04 · no `mcp_port` (batch 5a)

A channel module declares `mcp_server_name` only; `mcp_server` advertises `mcp_server_url(mcp_server_name)` and the host mounts it there. A plugin channel therefore never picks a port.

## 2026-09-04 · `module_registry` replaces `MODULE_MAP` (batch 5d)

The registry view is the only module table; usages renamed.

## 2026-09-07 — cleanup_for_agent purges the retired per-channel credential table; per-channel stats key

Batch 4d switched reads to channel_credentials but kept the legacy tables; deleting an agent left its pre-cutover bot token / app secret behind. The cleanup now also deletes the agent's rows from this channel's LegacyTable (credential_legacy.LEGACY_TABLES is the one list), best-effort so an install without the table still deletes the agent. The deletion stat is keyed channel_credentials.<channel> so six channels no longer collapse into one count in the report.
