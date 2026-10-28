---
code_file: plugins/builtin.channels.narramessenger/src/narranexus_plugins/narramessenger_module/descriptor.py
last_verified: 2026-10-14
stub: false
---

## 2026-10-14 — 声明 `reset_on_reenable=("matrix_since_token",)`

禁用期之后续用旧 /sync 游标会让 agent 回答禁用期间积压的全部消息（续传基线 0 不做历史过滤）。绑定从禁用回到启用时清掉游标
→ 下一次 connect 是无游标首次 sync，由连接时刻基线丢弃历史（见 [[matrix_trigger.py]]）。机制在通用 store（[[credential_store]]）。
注意该游标因名字含 token 被 store 归入 secret 一侧：清除在拆分 public/secret 之后才并入；行的密文不可读（`secret_error`）时密文侧清除会跳过，以免覆盖原密文（含 access token）——该行在 key 恢复后若直接续用会续旧游标，正常路径是重新绑定（详见 [[credential_store]]）。

## 2026-09-07 — 描述符收下消息来源；删掉模块级 `SOURCE`

新增四个字段：`reply_tools` / `row_prefix_template` / `reply_extractor_ref` /
`dedicated_trigger`。它们此前是 `narramessenger_module.py` 顶层一次
`MessageSourceRegistry.register(...)`（外裹 `except ValueError: pass`）。搬进描述符之后，
NarraMessenger 仍然是**一条记录**，而且发行版排除本插件就同时带走它的 handler——原来那张类变量
字典既不认 owner，也不受 `builtin_overrides` 约束。

`reply_extractor_ref` 是 `"pkg.mod:function"` 字符串，和 `trigger_ref` / `module_ref` 同
一形状：声明它不能 import 本渠道的 SDK，平台在第一次真正抽取回复时才解析
（`message_source_handler._lazy_extractor`）。

删掉模块级 `SOURCE = WorkingSource.register("narramessenger")`。注册收归
`channel/contributions.py:register_working_source`，由 `contribution.py` 在 boot 时调一
次——只对本发行版装了、`registry.json` 没禁的插件发生。

# narramessenger_module/descriptor.py — the channel as one record

## Intent

`DESCRIPTOR` / `CHANNEL` for builtin.channels.narramessenger: the trigger, module, credential manager (+ read method), bind/test service shape, credential schema (identity vs secret fields, external id) and UI row that the platform's registry views read. Class references are strings so registering never imports the narramessenger SDK; the trigger's `channel_name` and this name must agree (parity test).

## 2026-09-04 · `meta.storage = "generic"` (batch 4d.2)

Bindings live in `channel_credentials`; no mirror, no bespoke table.

## 2026-09-04 · bind input declared (batch 4d.3)

`bind_fields`: the single `bind_command` (pasted link/command, not stored) — the stored schema is the Matrix identity `do_bind` derives from it. `unbind_service=True` with `bind_takes="db"` routes unbind through `do_unbind(db, agent_id)` (gateway-side unbind included).

## 2026-09-04 · registers its WorkingSource (batch 4e)

`SOURCE = WorkingSource.register("narramessenger")` at import (and the `TriggerType` twin); the package `__init__` imports this module first so `WorkingSource.NARRAMESSENGER` exists before the module/trigger class bodies read it. The platform (`hook_schema`) seeds no channel names any more. `meta["contact_key"] = "matrix"`: the key this channel's ids live under in `contact_info.channels`, read by `channel_contact_utils.contact_channel_keys`.

## 2026-09-04 · `module_registry` replaces `MODULE_MAP` (batch 5d)

The registry view is the only module table; usages renamed.
