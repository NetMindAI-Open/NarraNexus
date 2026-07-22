---
code_file: src/xyz_agent_context/module/home_assistant_module/_home_assistant_impl/binding.py
last_verified: 2026-07-15
stub: false
---

# binding.py — 把 agent 的 HA 绑定解析成 HAClient

## 为什么存在

MCP 工具调 `resolve_client(db, agent_id)`:按 `agent_id` 读绑定行(和 Lark 一样,不经 module
instance)→ 解析 config_json 成 HAConfig → 返回就绪的 HAClient;失败则返回**给用户看的原因**
(未连接 / 配置损坏 / 连不上),由工具转告,而非抛异常。

## 关键点

- 绑定按 `HomeAssistantBindingRepository.get_by_agent(agent_id)` 解析——和 route 侧同一套,不依赖
  instance 是否已创建。
- 未绑定 → 返回 `NOT_CONFIGURED`(引导去配置页或 `home-assistant-setup` 技能)。
- **一 agent 一绑定(Agent 级,刻意选择)**:用户有多套 HA(家/办公室)时,不同 agent 各绑各的。
  未来增强方向是"全局默认 HA + per-agent override"的混合层(nice-to-have,非当前范围)。

## 上下游

- **被谁用**:`home_assistant_module` 的 4 个 MCP 工具。
- **依赖**:`repository.HomeAssistantBindingRepository`、`ha_client`、`schema.HAConfig`。
