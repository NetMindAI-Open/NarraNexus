---
code_file: src/xyz_agent_context/module/home_assistant_module/home_assistant_module.py
last_verified: 2026-07-14
stub: false
---

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
- `get_instructions` 用 base 默认(`self.instructions.format(...)`),故 prompts 里不能有裸 `{}`。

## 上下游

- **被谁用**:MODULE_MAP(`module/__init__`)、module_runner(端口 7810)。
- **依赖**:`_home_assistant_impl/{ha_client,binding}`、`prompts`、`repository`(经 binding)。
- **MVP 限制**:一 instance 一绑定(Agent 级)。云端多租户 per-user 绑定是 Phase-2(见 spec)。
