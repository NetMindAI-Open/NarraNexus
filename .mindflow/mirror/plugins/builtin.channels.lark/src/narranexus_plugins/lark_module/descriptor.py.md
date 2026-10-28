---
code_file: plugins/builtin.channels.lark/src/narranexus_plugins/lark_module/descriptor.py
last_verified: 2026-09-07
stub: false
---

## 2026-09-07 — 描述符收下消息来源；删掉模块级 `SOURCE`

新增四个字段：`reply_tools` / `row_prefix_template` / `reply_extractor_ref` /
`dedicated_trigger`。它们此前是 `lark_module.py` 顶层一次
`MessageSourceRegistry.register(...)`（外裹 `except ValueError: pass`）。搬进描述符之后，
Lark 仍然是**一条记录**，而且发行版排除本插件就同时带走它的 handler——原来那张类变量
字典既不认 owner，也不受 `builtin_overrides` 约束。

`reply_extractor_ref` 是 `"pkg.mod:function"` 字符串，和 `trigger_ref` / `module_ref` 同
一形状：声明它不能 import 本渠道的 SDK，平台在第一次真正抽取回复时才解析
（`message_source_handler._lazy_extractor`）。

删掉模块级 `SOURCE = WorkingSource.register("lark")`。注册收归
`channel/contributions.py:register_working_source`，由 `contribution.py` 在 boot 时调一
次——只对本发行版装了、`registry.json` 没禁的插件发生。

# lark_module/descriptor.py — the channel as one record

## Intent

`DESCRIPTOR` / `CHANNEL` for builtin.channels.lark: the trigger, module, credential manager (+ read method), bind/test service shape, credential schema (identity vs secret fields, external id) and UI row that the platform's registry views read. Class references are strings so registering never imports the lark SDK; the trigger's `channel_name` and this name must agree (parity test).

## 2026-09-04 · `meta.storage = "generic"` (batch 4d.2)

The descriptor now declares generic storage like every other channel: the platform has no channel left whose bindings live outside `channel_credentials`.

## 2026-09-04 · bind input declared (batch 4d.3)

`bind_fields`: `app_id`, `app_secret`, `brand` (select feishu|lark, required — no silent default) + optional `owner_email`; the generic route enforces the select before `do_bind` sees it.

## 2026-09-04 · registers its WorkingSource (batch 4e)

`SOURCE = WorkingSource.register("lark")` at import (and the `TriggerType` twin); the package `__init__` imports this module first so `WorkingSource.LARK` exists before the module/trigger class bodies read it. The platform (`hook_schema`) seeds no channel names any more. `meta["agent_instance"]` declares the agent-level LarkModule instance (description/keywords/topic_hint) the instance factory creates for every agent — the factory no longer names Lark.

## 2026-09-04 · no `agent_instance` meta (batch 5b.2)

The module declares its agent-level instance in its own `ModuleConfig`.

## 2026-09-04 · `module_registry` replaces `MODULE_MAP` (batch 5d)

The registry view is the only module table; usages renamed.

## 2026-09-07 — owner_email is an email field

Format validated by the shared bind validator (me/my/I resolution silently failed on a malformed address).
