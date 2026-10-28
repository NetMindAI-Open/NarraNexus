---
code_file: plugins/builtin.home_assistant/src/narranexus_plugins/home_assistant_module/home_assistant_module.py
last_verified: 2026-09-04
stub: false
---
## 2026-08-11 (PR-G) — 4 个 HA 工具去 db，走 seam

`ha_list_entities/ha_get_entity/ha_list_services/ha_call_service` 不再 `get_mcp_db_client()` + `resolve_client(db, …)`，改 `resolve_client(agent_id)`（[[binding]] 内部经 ChannelCredentialStore seam）。本模块 get_mcp_db_client == 0。HA 无 bind/unbind MCP 工具（绑定 100% 后端面板 PUT /binding），故无写留尾。


# home_assistant_module.py — 经 Home Assistant 查/控智能家居

## 为什么存在

让 agent 能查询/控制用户家里的智能设备。capability 模块(自动加载),暴露 4 个 MCP 工具代理 HA 的
REST API。**只对接 HA 的 Apache-2.0 API**(不碰 Xiaomi/Miloco 受限代码,品牌无关);不直连
miloco-miot 是因为其 License 禁商用/软件(设计记录为作者本地)。

## 关键点

- **MCP 端口 7810**;4 工具:`ha_list_entities` / `ha_get_entity` / `ha_list_services` / `ha_call_service`,
  分别映射 `GET /api/states`、`GET /api/states/{id}`、`GET /api/services`、`POST /api/services/{d}/{s}`。
- 工具都 `agent_id` 作参数(module_system §5),经 `binding.resolve_client(db, agent_id)` 拿到 HAClient;
  未绑定则返回可读的 `NOT_CONFIGURED` 提示让 agent 转告用户。
- **场景逻辑不在这**(铁律 #4):模块只做通用查/控;"客厅灯 8 点开"这类进各 agent 的 Awareness。
- **写操作要确认**:prompts + 工具 docstring 要求 agent 对高影响动作(门锁/安防/车库)先向用户确认。
- `contribute_instructions` 用 base 默认(`self.instructions.format(...)`),故 prompts 里不能有裸 `{}`。

## 上下游

- **被谁用**:module_registry(`module/__init__`)、module_runner(端口 7810)。
- **依赖**:`_home_assistant_impl/{ha_client,binding}`、`prompts`、`repository`(经 binding)。
- **MVP 限制**:一 instance 一绑定(Agent 级)。云端多租户 per-user 绑定是 Phase-2(见 spec)。

## 2026-09-04 · no per-module port (batch 5a)

The MCP server URL comes from `mcp_server_url("<server_name>")` (the single MCP host + `/mcp/<server_name>/sse`); the module-level port constant / `self.port` and the factory's `port` parameter are gone.

## 2026-09-04 · declares its platform metadata (batch 5b)

`get_config()` is static and carries what the platform used to table about this module (default / base / always-load membership, instance prefix, role, display, decision metadata as applicable).

## 2026-09-04 · declares its agent-level instance (batch 5b.2)

`agent_instance` in `get_config()` (description / keywords / topic hint) replaces the factory's or descriptor's copy; the InstanceFactory creates it for every agent.
