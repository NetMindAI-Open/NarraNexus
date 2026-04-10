---
code_file: src/xyz_agent_context/module/social_network_module/_social_mcp_tools.py
last_verified: 2026-04-10
---

# _social_mcp_tools.py — SocialNetworkModule MCP 工具定义

## 为什么存在

从 `social_network_module.py` 分离出来（2026-03-06 重构），把 MCP 工具注册逻辑与 Module 的 Hook 生命周期解耦。提供四个工具：`extract_entity_info`（主动录入实体信息）、`search_social_network`（检索联系人）、`get_contact_info`（获取联系方式）、`get_agent_social_stats`（查看 Agent 的社交概况）。

## 上下游关系

- **被谁用**：`SocialNetworkModule.create_mcp_server()` 调用 `create_social_network_mcp_server(port, get_mcp_db_client, SocialNetworkModule)` 返回 FastMCP 实例；`ModuleRunner` 部署该实例
- **依赖谁**：`InstanceRepository`（按 `agent_id + module_class` 查找实例 ID）；`SocialNetworkModule` 类引用（通过 `module_class` 参数传入，实例化临时 Module 对象操作数据）

## `agent_id` 如何传入

所有工具都要求显式传入 `agent_id`。MCP 工具在独立进程里没有"当前 Agent 上下文"，LLM 需要从系统提示里（`SOCIAL_NETWORK_MODULE_INSTRUCTIONS` 里的 `Your agent_id is {agent_id}` 提示）读取并传入。工具内部通过 `InstanceRepository.get_by_agent(agent_id, "SocialNetworkModule")` 找到 `instance_id`，再用它隔离该 Agent 的实体数据。

## 设计决策

**临时 Module 实例模式**：`_get_instance_and_module()` 辅助函数在每次工具调用时临时创建一个 `SocialNetworkModule` 实例（`module_class(agent_id=agent_id, database_client=db, instance_id=instance_id)`），用完即弃。这避免了在 MCP 进程里持有跨请求的状态。代价是每次工具调用都有实例化开销，但 `SocialNetworkModule.__init__` 很轻量（只是字符串替换和 lazy 初始化），可接受。

**`extract_entity_info` 的标签纪律**：工具 docstring 里明确要求"每次更新最多加 2-3 个标签，多数更新加零个标签"，并提示使用规范形式（如 `expert:recommendation_system` 而不是 `expert:recommender_systems`）。这是为了控制标签膨胀——LLM 倾向于每次交互都添加新标签，最终导致一个实体有几十个噪声标签而失去检索价值。

**三种检索模式**：`search_social_network` 支持 `exact_id`（精确 ID）、`tags`（标签关键词）、`semantic`（向量语义），默认 `auto`（自动检测）。`auto` 逻辑：如果关键词以 `user_` 或 `entity_` 开头，走精确 ID；否则走标签搜索。向量语义检索需要额外的 `get_embedding()` 调用，是最慢但最准的模式。

## Gotcha / 边界情况

- **`module_class` 参数**：`create_social_network_mcp_server` 接受 `module_class` 而不是 `module_instance`。这是为了避免循环导入——如果直接在参数里实例化 Module，会在 import 时触发所有依赖的初始化。通过类引用延迟到调用时实例化，打破了循环。

## 新人易踩的坑

- `get_contact_info` 工具返回的是结构化的 `contact_info` 字典（存储在 `identity_info.contact_info` 字段），不是 `entity_description` 里的自然语言描述。两者都可能包含联系方式，但格式和来源不同——前者是 Agent 主动通过 `extract_entity_info` 结构化写入的，后者是 hook 自动提炼的自然语言。
