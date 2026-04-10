---
code_file: backend/routes/agents_social_network.py
last_verified: 2026-04-10
stub: false
---

# agents_social_network.py — 社交网络实体查询路由

## 为什么存在

`SocialNetworkModule` 维护 Agent 认识的人/组织的档案，存储在 `instance_social_entities` 表。这个路由暴露三个只读接口：查询单个实体的详细信息、列出所有实体、关键词/语义搜索。这些接口服务于前端的社交网络面板，以及开发者调试社交记忆的需求。

## 上下游关系

- **被谁用**：`backend/routes/agents.py` 聚合；前端社交网络面板
- **依赖谁**：
  - `InstanceRepository` — 查询 `SocialNetworkModule` 实例 ID
  - `SocialNetworkRepository` — 语义搜索（`semantic_search`）和关键词搜索（`keyword_search`）
  - `xyz_agent_context.agent_framework.llm_api.embedding.get_embedding` — 语义搜索时生成 query 的向量
  - `xyz_agent_context.utils.db_factory.get_db_client` — 直接查询 `instance_social_entities` 表

## 设计决策

**路由注册顺序**

`/{agent_id}/social-network/search` 必须在 `/{agent_id}/social-network/{user_id}` 之前注册，否则路径匹配时 "search" 会被当成 user_id 的字符串值，把搜索请求路由到单实体查询接口，导致查不到结果但也不报错。注释里专门标注了这个要求。FastAPI 在同一路由器内按注册顺序匹配，不按路径特异性排序。

**硬限 1000 条**

`get_all_social_network_entities` 用 `limit=1000` 硬限制最大返回数量。对于正常使用场景（Agent 通过日常对话积累的社交关系，通常是几十到几百条）这够用，但如果一个 Agent 接入了大型通讯录，1000 条可能不够。目前没有分页接口。

**语义搜索即时 embedding**

搜索时调用 `get_embedding(query)` 实时生成向量，这会产生一次 LLM API 调用（embedding 接口）。如果 embedding 服务不可用，语义搜索会抛异常，前端需要处理。关键词搜索不依赖外部服务，更稳定。

## Gotcha / 边界情况

- **只查第一个实例**：如果一个 Agent 有多个 `SocialNetworkModule` 实例（理论上可能，虽然实践中通常只有一个），这里只用 `instances[0]` 的实例 ID。其他实例的社交实体不会被查询到。
- **`_parse_json` 处理双重编码**：代码里有处理 JSON 双重编码的逻辑（`json.loads` 结果如果还是字符串，再 `json.loads` 一次）。这说明历史数据里存在 `identity_info` 等 JSON 字段被双重序列化的情况，是历史遗留问题。

## 新人易踩的坑

单实体查询接口用 `user_id` 作为路径参数，但实际上查的是 `entity_id` 字段（`WHERE entity_id = {user_id}`）。这个接口的命名继承自最初只处理"用户"类型实体的设计，实际上 `entity_id` 可以是任何类型实体的 ID，不限于用户。
