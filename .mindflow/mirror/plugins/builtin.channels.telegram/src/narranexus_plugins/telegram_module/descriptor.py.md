---
code_file: plugins/builtin.channels.telegram/src/narranexus_plugins/telegram_module/descriptor.py
last_verified: 2026-09-07
stub: false
---

## 2026-09-07 — 描述符收下消息来源；删掉模块级 `SOURCE`

新增四个字段：`reply_tools` / `row_prefix_template` / `reply_extractor_ref` /
`dedicated_trigger`。它们此前是 `telegram_module.py` 顶层一次
`MessageSourceRegistry.register(...)`（外裹 `except ValueError: pass`）。搬进描述符之后，
Telegram 仍然是**一条记录**，而且发行版排除本插件就同时带走它的 handler——原来那张类变量
字典既不认 owner，也不受 `builtin_overrides` 约束。

`reply_extractor_ref` 是 `"pkg.mod:function"` 字符串，和 `trigger_ref` / `module_ref` 同
一形状：声明它不能 import 本渠道的 SDK，平台在第一次真正抽取回复时才解析
（`message_source_handler._lazy_extractor`）。

删掉模块级 `SOURCE = WorkingSource.register("telegram")`。注册收归
`channel/contributions.py:register_working_source`，由 `contribution.py` 在 boot 时调一
次——只对本发行版装了、`registry.json` 没禁的插件发生。

# telegram_module/descriptor.py — the channel as one record

## Intent

`DESCRIPTOR` / `CHANNEL` for builtin.channels.telegram: the trigger, module, credential manager (+ read method), bind/test service shape, credential schema (identity vs secret fields, external id) and UI row that the platform's registry views read. Class references are strings so registering never imports the telegram SDK; the trigger's `channel_name` and this name must agree (parity test).

## 2026-09-04 · generic store as the source of truth (batch 4d.1)

`meta.storage = "generic"`: the manager persists in `channel_credentials`, so the dual-write mirror skips this channel.

## 2026-09-04 · bind input declared (batch 4d.3)

`bind_fields`: `bot_token` (secret) + optional `owner_username` — the `do_bind` keyword arguments the generic bind passes through.

## 2026-09-04 · registers its WorkingSource (batch 4e)

`SOURCE = WorkingSource.register("telegram")` at import (and the `TriggerType` twin); the package `__init__` imports this module first so `WorkingSource.TELEGRAM` exists before the module/trigger class bodies read it. The platform (`hook_schema`) seeds no channel names any more.

## 2026-09-04 · `module_registry` replaces `MODULE_MAP` (batch 5d)

The registry view is the only module table; usages renamed.
