---
code_file: src/narranexus/platform/repository/agent_registry_repository.py
last_verified: 2026-09-04
stub: false
---

# agent_registry_repository.py — `bus_agent_registry` 的数据访问

## 2026-08-05 — review 收口：改名 `get_profile`、进包导出、policy 位置更正

- 读取方法从 `get()` 改名 `get_profile()`：`get` 与基类 `get_by_id` 的命名习惯
  打架（基类没有 `get`，所以不是覆盖冲突，但读起来像）。
- 加进 `repository/__init__.py` 的导出面，与目录里多数 repo 一致。
- 文件 docstring 里 policy 的位置从 `services/` 更正为
  [[agent_discovery_sync]]（已搬进 message_bus 包）。

## 为什么存在（为什么不在 message_bus 模块里）

这张表原来只有一个写入者：`MessageBusModule.gather` 里的一段内联
代码。于是"能不能被同伴发现"变成了**跑过一轮的副作用**——刚创建、刚配置好但还
没跑的 agent 在名录里根本不存在；而那唯一的写入者还把 `capabilities` 硬编码成
`[]`（P1 段02，详见 [[agent_discovery_sync]]）。

修法要求创建路径和配置路径也能写这张表：一个 HTTP 路由、一个 Awareness MCP
工具、技能安装器。它们不该伸手进某个模块的内部（铁律 #3：模块相互独立、可热
插拔），而按项目分层，**repository 从不住在模块里面**。所以表访问搬到这里，
"这一行该写什么"的策略在上层的 [[agent_discovery_sync]]。

`LocalMessageBus` 保留自己那套面向 bus API 的读写
（`register_agent` / `get_agent_profile` / `search_agents`）；本 repo 是**平台侧**
保持这行为真的 seam。

## 设计决策

- **行实体复用 bus 的 `BusAgentInfo`**（一张表一个模型，胜过两个会漂移的），
  但**惰性 import**：`narranexus.platform.message_bus.__init__` 会拉进
  LocalMessageBus、trigger 和 channel 注册表，一个只是创建 agent 的 HTTP 路由
  没理由加载这些。这也是 bus 自己需要 repository 时用的同一套惯例。
- `upsert_profile` 保留已有行的 `registered_at`——"第一次出现"和"最后一次同步"
  是两个事实。`last_seen_at` 每次刷新。
- `capabilities` 存 JSON 文本；读时 `JSONDecodeError` 吞成 `[]`：一行被手工编辑
  过不该让所有人的发现功能崩掉。

## Gotcha

`BaseRepository` 的客户端属性叫 `self._db`（不是 `self.db`）——写这个文件时就
在这上面栽过一次。
