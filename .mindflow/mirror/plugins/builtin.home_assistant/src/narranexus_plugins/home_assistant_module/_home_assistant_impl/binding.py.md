---
code_file: plugins/builtin.home_assistant/src/narranexus_plugins/home_assistant_module/_home_assistant_impl/binding.py
last_verified: 2026-08-11
stub: false
---
## 2026-08-11 (PR-G) — 接入 ChannelCredentialStore seam；resolve_client 去 db 参数

HA 无凭据管理器类（存 JSON config blob），新增薄适配器 `HomeAssistantCredentialManager(db).get(agent_id)` + `_HABindingCred`（`to_raw_dict`={config_json}，**原样带 blob**，不铺平——保留 corrupt/not-configured 区分且密钥 LLAT 在 blob 内）+ `_cred_from_raw`。`resolve_client(agent_id)` **去掉 db 参数**，改经 [[channel_store]] seam 取 config_json（本地 DirectStore、云端 HttpStore 打 owner-gated 端点），再 `HAConfig.model_validate_json` → HAClient，逻辑与旧版逐字一致。两个调用方（mcp 4 工具 + backend /verify）同步改。


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
