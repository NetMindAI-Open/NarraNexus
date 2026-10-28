---
code_file: backend/integrations/plugins/__init__.py
last_verified: 2026-08-28
stub: false
---

# `plugins/__init__.py` — package boundary marker for the plugin-install subsystem

## 为什么存在

`plugin_paths.py`（`agent_framework`）只定义了 Claude Code / Codex 落在磁盘
上的**位置**；谁来决定装什么版本、怎么装、装完怎么探测、装失败怎么解释给
用户看，这一整条编排逻辑必须落在 `backend`（铁律 #21：被 routes 消费的安装
逻辑不能塞进 `xyz_agent_context`，那样会让 agent 侧进程间接依赖 pip/npm 子
进程语义）。这个包就是那条编排逻辑的家。

## 上下游关系

- **被谁用**：Phase 3 的安装/状态路由（backend/routes 下，本次未实现）会
  只 import `service.PluginService`；不会绕过它直接碰 `_installers/`。
- **依赖谁**：`registry.py` 依赖 `narranexus.platform.agent_framework.
  adapters.claude.cli_binary.PINNED_CLI_VERSION`（版本单一真值）；
  `_installers/*.py` 依赖 `narranexus.platform.agent_framework.plugin_paths`
  （落点单一真值）。除此之外本包不 import 任何 `xyz_agent_context` 之外的
  agent 侧代码，也绝不反向被 `xyz_agent_context` import。

## 设计决策

- 五个公开文件（`spec.py` / `registry.py` / `errors.py` / `service.py` +
  私有 `_installers/`）严格分层：spec 是纯数据契约，registry 是数据，
  `_installers` 是策略实现，`errors` 是失败翻译，`service` 是唯一门面。
  这样 Phase 3 的路由只需要认识 `service.PluginService` 一个类。

## 相关约束

- 铁律 #21 —— 本包的存在理由本身
- 铁律 #23 —— 结构化子包而非在 `backend/integrations/` 下摊平文件
