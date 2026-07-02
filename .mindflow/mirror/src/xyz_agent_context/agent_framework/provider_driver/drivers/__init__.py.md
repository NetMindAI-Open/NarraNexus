---
code_file: src/xyz_agent_context/agent_framework/provider_driver/drivers/__init__.py
last_verified: 2026-06-17
stub: false
---

## 2026-06-17 — register codex_oauth driver

在 import 列表中新增 `codex_oauth`，让其 `@register` 装饰器在包导入时触发，
注册表多出一个 `driver_type`。意图是把 Codex(OAuth) 接入 provider 轴，与已有的
`claude_oauth` 并列，作为新的 endpoint/key 解析来源。和此处其他 driver 一样靠
import 副作用注册——定义了但没被这里 import 的 driver 不会进注册表。一行新增，
注册机制不变。

# drivers/__init__.py — import-time registration

Imports each Driver module so its ``@register`` decorator fires. Order
of imports inside this file is irrelevant — every Driver maps to a
distinct ``driver_type`` so there's no last-write-wins collision.

``system.py`` is included unconditionally; the cloud-mode gate is
inside that module so a local install gets the import but not the
registration. This means a future "elevate this local instance to
cloud" toggle wouldn't need a different bootstrap path.
