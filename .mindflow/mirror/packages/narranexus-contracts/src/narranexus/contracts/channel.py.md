---
code_file: packages/narranexus-contracts/src/narranexus/contracts/channel.py
last_verified: 2026-10-14
stub: false
---

## 2026-10-14 — `CredentialSchema.reset_on_reenable`：禁用期间的传输状态不许续用

可选字段 `reset_on_reenable: tuple[str, ...] = ()`（默认空，既有描述符行为不变）：列出绑定从**禁用变回启用**时平台要
清成 "" 的存储值名。动机是流式游标——Matrix 的 `matrix_since_token` 在禁用期之后被续用，Synapse 会把禁用期间
（数天/数周）的消息一次性送回，agent 会逐条回答过期消息。**挂在「禁用→启用」迁移而不是「禁用」动作上**：行变成禁用有很多
条路（set-active 路由、`disable_credential`、bundle 导入一律落 `enabled=0` 且带着导出的游标、Lark 自己 `patch(enabled=False)`、
声明出现之前就已禁用的存量行），并没有「禁用咽喉」；而回到启用的所有写法都在通用 store 里（见 [[credential_store]]）。
**不做**：仍启用的行、全新绑定、每次 sync。

## 2026-09-07 — 渠道的「消息来源」变成描述符的字段；新增 MessageSourceSpec

新增 `MessageSourceSpec`（哪些工具算「回复了」、行前缀模板、惰性 extractor ref、是否有
专属 trigger），并把同一组字段挂到 `ChannelDescriptor` 上，由 `message_source` 属性投影
成一条 spec。

为什么放进契约：这些事实此前只存在于各渠道模块顶层的
`MessageSourceRegistry.register(...)` 调用里——一张没有 owner、不进 slot 树、不受
`registry.json` / 发行版约束的类变量字典。谁答某个 source，取决于这个进程碰巧 import
过谁：没 import 到时一条已送达的 Lark 回复会落到默认 handler，被记成 NO-REPLY，且无
任何日志。做成描述符字段之后，渠道仍然是**一条记录**，发行版排除这个渠道就同时带走它
的 handler。

两点刻意的取舍：

- `display_name` 兼作品牌 label。原来 `display_name` 和 `display_label` 是同一事实的两
  种拼写，靠人手对齐；六个内置渠道的两个值本来就完全一样。
- `reply_extractor_ref` 是 `"pkg.mod:function"` 字符串而非可调用对象，和
  `trigger_ref` / `module_ref` 同一形状：**声明一个 extractor 不能 import 渠道的 SDK**，
  解析推迟到第一次真正抽取回复时。

非渠道的来源（消息总线、Job 时钟）用同一个 `MessageSourceSpec`，走
`ingress.message_sources` 位；`chat` / `a2a` / `callback` / `skill_study` 什么都不声明——
它们本来就没有自己的回复工具，默认 handler 就是正确答案。

# contracts/channel.py — ChannelDescriptor (slot `ingress.channels`)

## Intent

A channel was six scattered facts (trigger map row, module_registry entry, data-access `CHANNELS` row, `WorkingSource` member, message-source handler, frontend row). `ChannelDescriptor` is that record as data: name (= the inbound WorkingSource value), transport (`socket` / `poll` / `webhook` / `none`), `CredentialSchema` (fields split into public identity and secrets; `external_id_field` is the channel-wide unique bot/app id), lazy `pkg.mod:Class` references for the trigger / module / credential manager / bind-test service, and `ChannelUi`. Every platform table that used to name channels is now a view over the registry; a channel plugin is one descriptor plus its classes. Batch 4b builds the generic credential store and routes on `credential_schema`; 4c the webhook transport on `transport`.

## 2026-09-04 · `bind_fields` (batch 4d.3)

`ChannelDescriptor.bind_fields` declares what a bind CALL takes when it differs from the stored credential shape — a manager-backed channel's service binds from e.g. a pasted link (NarraMessenger) or resolves an owner e-mail (Slack/Lark) and stores something else. Empty means the stored schema is the bind input (plugin channels). The generic route validates bodies against it (`credential_store.bind_fields_for` / `validate_bind_fields`) and exposes it in the schema view so a UI can render the bind form for any channel.

## 2026-09-04 · `module_registry` replaces `MODULE_MAP` (batch 5d)

The registry view is the only module table; usages renamed.

## 2026-09-07 — channel name is ASCII [a-z0-9_]

__post_init__ validates with a regex: str.isalnum() is Unicode-aware, so a name like 'café' passed a check whose message promised [a-z0-9_] and then became an invalid SQL alias / URL segment.

## 2026-09-07 — FieldKind gains email

An email kind so descriptors can declare the format and the shared validator enforces it.
