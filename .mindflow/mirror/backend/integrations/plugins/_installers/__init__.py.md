---
code_file: backend/integrations/plugins/_installers/__init__.py
last_verified: 2026-08-28
stub: false
---

# `_installers/__init__.py` — 私有策略实现子包边界

## 为什么存在

标记 `_installers/` 是私有实现(下划线前缀,铁律 #23的约定)——`service.py`
是唯一允许直接实例化 `PipTargetInstaller` / `NpmPrefixInstaller` 的地方,
其他任何模块（尤其是 Phase 3 路由)不应该 import 这个子包里的任何东西。

## 上下游关系

- **被谁用**：只被同包内的 `service.py` 使用。
- **依赖谁**：无——纯粹的包边界标记,不含逻辑。

## 相关约束

- 铁律 #23 —— 私有实现放 `_*_impl/`（此处命名 `_installers/`，同一约定的
  变体：以"策略实现集合"而非"某个 service 的私有细节"命名，语义更贴切）。
