---
code_file: src/narranexus/platform/channel/credential_store.py
last_verified: 2026-10-14
stub: false
---

## 2026-10-14 — 禁用→启用迁移时清 `reset_on_reenable` 声明的字段（同一次版本化写）

行回到启用的三种写法都在本文件，各自检测「旧行 enabled=0 且本次 enabled=True」并把
`descriptor.credential_schema.reset_on_reenable` 里的值清成 ""：`set_enabled(True)`（经 `patch`）、`patch(..., enabled=True)`
（Lark 的 `is_active` 翻转走这条）、`upsert(..., enabled=True)` 盖在已禁用的行上。清除与该次写入是同一次 `version+1`。
全新绑定（无旧行）、仍启用的行（含对已启用行再次启用）、仍禁用的行都不动；没声明的渠道（含 Lark）零变化。
为什么不在 `set_enabled(False)` 清：bundle 导入 `upsert(enabled=False)` 带着源环境游标、Lark 自己 `patch(enabled=False)`、
老的已禁用行都绕过它；而且禁用后订阅者最晚一个轮询周期才停，期间迟到的游标写入落在已禁用行上——在迁移处清，这些都无害，
不需要每次 sync 多读一次库的写前检查。契约见 [[channel]]。
**不可读密文（`secret_error`）**：重置在拆分之后才并入，且密文侧的重置在 `secret_error` 时跳过——否则 `matrix_since_token`（名字含 token，落在 secret 一侧）会让 `secret` 非空、绕过 fail-closed 守卫，把原密文（含 access token）覆盖成只含空游标的新 blob。不可读行本来就连不上（`list_active` 跳过），公共侧的重置照常；密文侧游标在该行被重新绑定前不会被清（key 恢复后若直接续用，会续旧游标——已记为残余风险，正常路径是重新绑定）。`upsert` 是整行重绑，不受影响。
锁：`tests/channel/test_credential_reset_on_reenable.py`、`tests/bundle/test_channel_credentials.py::test_imported_narramessenger_cursor_is_blank_once_activated`。

## 2026-09-09 — `set_enabled(..., reason=)` 写通用 `disabled_reason`；`patch` 对不可读密文 fail-closed（B-28，复审 C1/I4）

`disabled_reason` 是频道无关的"平台把凭据关掉了，原因是什么"记录：`set_enabled` 走版本化的 `patch`，
禁用时把 reason 写进 public 值，启用时清成 ""——generic `set-active` 路由的用户重新启用自然清掉。
**C1 教训**：`patch` 以前把 `{**public, **secret}` 重新 `encode_secrets` 写回，而 `_row_to_record` 对解不开的
密文把 `secret` 置空只记 `secret_error`，于是任何 patch（含开关一下）都会把原密文覆盖成 ""——key 轮换事故从
"恢复 key 即可"变成"全员重绑"。现在 `patch` 直接读原始行：`secret_error` 非空且本次 patch 不带新的 secret
字段时，`secret_json` 原样回写（逐字节相等），只有 re-bind 带来的新 secret 才允许替换；`enabled` / public /
`version` 语义不变（version 仍自增，`list_active` 的一次性警告按 version 去重不受影响）。
**已知边界（复审 M3）**：放行条件是"本次 patch 不带任何 secret 字段"；对双密钥频道（Slack bot_token+app_token）
在密文不可读时若某次 patch 只带其中一个新密钥，会写出只含一个字段的新密文、另一个丢失——今天 bind 走
`upsert` 全量值打不到，写下来是因为"只带部分 secret 的 patch"是合法调用形状。
`set_enabled` 写入前对 reason 做一次 `str()`（PR #388 round-2 M6：前端/插件消费方据此可依赖字段恒为字符串，
不必各自 `typeof` 守卫）。`set_enabled` 捕获 `patch` 的版本竞争 `RuntimeError` → `logger.error` + 返回 False，保持"永不抛、False =
没做成"的旧契约（路由已有 False 分支；trigger 对 False 打 ERROR）。测试：
`tests/channel/test_credential_store_unreadable.py::test_set_enabled_never_rewrites_an_unreadable_secret`、
`::test_patch_keeps_an_unreadable_secret_unless_a_new_secret_is_given`。

## 2026-09-07 — 删掉 `descriptor_for` / `all_descriptors` 里的死 import

两处 `import narranexus.platform.module_system  # noqa: F401 — registers the builtin
descriptors (idempotent)` 已删。`register_all` 早就不存在了，这个 import 什么也不注册，
注释描述的是一份代码已经没有的契约。

行为**没有**变化，这一点由 `tests/channel/test_unbooted_process_fails_loud.py` 钉住：没
boot 的进程里 `descriptor_for` 照旧抛 `UnknownChannel`（响亮，不是「悄悄当作存在」），
`all_descriptors` 照旧返回空元组——空是诚实的答案（这个进程不知道任何渠道），配合前一条
就不会被误读成「而且查询还能用」。

# channel/credential_store.py — GenericCredentialStore

## Intent

The ONE credential store every IM channel is served from (table `channel_credentials`, one row per (channel, agent)). Values are split by the channel's `CredentialSchema`: declared secrets — and anything that looks like one (token/secret/password/key) unless declared public — go encrypted into `secret_json`, identity fields into `public_json`, the schema's `external_id_field` into the channel-wide unique `external_id`. Descriptors come from `ingress.channels` (an unknown channel is `UnknownChannel`). Plugin channels write here through the generic routes; since batch 4d the six builtin managers persist here too (the retired per-channel tables are copied in once by [[credential_legacy]]).

## 2026-09-04 · webhook transport (batch 4c)

`CredentialRecord.app_id` (external id, else agent id) — the subscriber key `ChannelTriggerBase` expects on a credential, so generic-store records drive a trigger directly.

## 2026-09-04 · generic store as the source of truth (batch 4d.1)

`patch(channel, agent_id, fields, expect=)` is an optimistic field-level merge: the row carries `version`, a write only lands when the version it read is still current, else re-read and retry — so two writers on DISJOINT fields (a bind panel and a trigger's status update) never clobber each other; `expect` makes it a compare-and-set (`update_if`). `find_one(channel, external_id=…, **public)` answers the channel-wide 'is this bot already bound?' question. `upsert` bumps the version and stamps `created_at` on insert.

## 2026-09-04 · every builtin channel reads here; `list_for_agent` (batch 4d.2)

NarraMessenger and Lark joined the four managers switched in 4d.1, and `credential_mirror` is deleted — there is no second copy of any binding any more. `list_for_agent(agent_id)` returns an agent's bindings across channels (bundle export, agent deletion); records decode secrets for the caller, so the caller decides what leaves the process (the bundle exporter ships them only under the explicit opt-in).

## 2026-09-04 · bind-input validation (batch 4d.3)

`bind_fields_for(descriptor)` (the descriptor's `bind_fields`, else the stored schema) and `validate_bind_fields(descriptor, values)` — unknown names rejected (fail-closed: they would reach a service's `do_bind(**fields)` as a TypeError), required non-blank, select within options — are what the generic bind route checks before any service runs.

## 2026-09-04 · `all_descriptors` (batch 4e)

`all_descriptors(registries=None)` lists every registered channel descriptor — the one registry read the instance factory, contact utils, dashboard and manyfold export use instead of naming channels.

## 2026-09-07 — per-row decrypt guard: secret_error / readable

SecretBox.decrypt fails closed (raises) on a Fernet token this key cannot open. Raising out of list_active turned one bad row — often another user's — into a whole-channel outage (the watcher retried forever). _row_to_record now catches the decrypt failure per row and returns the record with secret={} and secret_error='… re-bind required'; list_active skips such records (one warning per (channel, agent, version)), get()/find_one()/list_all() return them with the flag so callers can surface 'credential unreadable'. Fail-closed is preserved: an unreadable record never reaches a transport as an empty-but-valid credential.

## 2026-09-07 — CredentialConflict

upsert translates the DB unique-index violation ((channel, agent_id) / (channel, external_id)) into CredentialConflict(channel, external_id, agent_id); the DB stays the enforcement point (no application pre-check — that is the race), the route maps it to 409.

## 2026-09-07 — validate_bind_fields checks formats by field kind

email / url / int kinds are format-checked for every channel through the descriptor (the Lark bind used to check '@' in owner_email by hand and lost it in the generic migration).
