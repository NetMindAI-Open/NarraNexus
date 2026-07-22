---
code_file: src/xyz_agent_context/agent_framework/provider_driver/__init__.py
last_verified: 2026-07-20
stub: false
---
## 2026-07-20 — 取消 CallContext 导出（死代码清理）

`CallContext` 从 import 和 `__all__` 中移除。它只服务于 `on_call_completed`
这个从未被调用的驱动层计费钩子，随该钩子一并删除，见 [[base]] / [[system]]。
包的公开面其余不变。

# __init__.py — package facade

Re-exports the public API so callers can write
``from xyz_agent_context.agent_framework.provider_driver import resolve_user_llm_configs``
or ``resolve_user_runtime_llm_configs`` without reaching into submodules.
Imports ``drivers`` to trigger ``@register`` side effects — by the time
the resolver runs the registry is populated.

The import order matters only in one way: registry must be imported
before drivers (so the decorator exists). Python's module loader
gives us that for free because ``drivers/__init__.py`` imports
``provider_driver.registry`` transitively via ``base``.
