---
code_file: src/narranexus/platform/agent_framework/providers/framework_binding.py
last_verified: 2026-09-10
stub: false
---

## 2026-09-10 — `SUBSCRIPTION_AUTH_TYPES` 改为 re-export

常量本体回到 [[provider_schema]]（`AuthType` 旁，枚举值子集，不是注册表规则），本文件 import 后保留在
`__all__` 里 re-export，`framework_can_drive_provider` 继续用它。理由见 provider_schema 2026-09-10 条目：
`api_config` 与 claude driver 也要用同一集合，而本文件顶层 import `loop.driver`，让 `api_config` 反向 import
本文件会把框架注册表拉进配置层。

# framework_binding.py — which card may be bound to which slot

## Why it exists (2026-09-07, round-2 P2-I8 / P2-I2)

`SLOT_REQUIRED_PROTOCOLS`, `SUBSCRIPTION_AUTH_TYPES`, `get_slot_required_protocols` and
`framework_can_drive_provider` lived in `platform/schema/provider_schema.py` and reached UP into
`agent_framework.loop.driver` through imports inside the function bodies — the code even said so
(`# Local import: the framework registry lives above the schema layer`). That inverted the declared
one-way stack (`api → runtime → service → impl → repository → schema`) and, because import-linter's
contracts are static AST analysis, the inversion was invisible to every gate the repo has. Here the
imports are top-level and the direction is right; `provider_schema` is enums and pydantic models
again, so anything may import it without pulling in the framework registry.

## The error contract is the other half

This module is the CONFIG boundary. An unknown framework, or one a binding names but no plugin
provides, raises `ValueError`, which the provider routes already map to 400. Making misbindings loud
(`FrameworkNotInstalledError`, a `RuntimeError`) landed in a previous round without this conversion,
so a user who uninstalled a bound framework got a 500 on the whole provider/settings page — the only
recovery path from a bad binding was the page the bad binding broke.

Deliberately NOT done: widening this into "unknown framework → default". The binding is refused,
never substituted — silently running the user's agent on a framework they did not choose is the
failure this whole area exists to prevent. And the raw `FrameworkNotInstalledError` stays on the TURN
path (`driver.get_agent_loop_driver`), where its message is the actionable one.

`resolved_framework_name()` is the boundary-flavoured twin of `driver.resolve_framework_name()`: same
precedence (explicit > env > binding > code default), 400-shaped errors. `user_service.set_slot` uses
it for exactly that reason.

## Gotchas

* `framework_can_drive_provider`'s subscription gate compares `FrameworkMeta.name` on both sides, so
  case never enters into it (the framework slot is case-insensitive).
* Frontend twin: `frontend/src/lib/agentFramework.ts` `providerBacksFramework()`. Keep them in step
  or the picker offers bindings the writers reject.
