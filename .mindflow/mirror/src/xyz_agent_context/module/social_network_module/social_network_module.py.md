---
code_file: src/xyz_agent_context/module/social_network_module/social_network_module.py
last_verified: 2026-06-08
---

## 2026-06-08 — entity 折进统一记忆引擎（overhaul task 1）

删了 `_feed_entity_to_engine` 及其调用点。原来实体写两处（`instance_social_entities`
真身 + `memory_entity` 镜像），镜像只喂"当前对话对象"、漏了被提及的第三方
→ 统一 `remember` 搜不到他们。现在 `SocialNetworkRepository` 直接在
`memory_entity` 上 CRUD（见 [[social_network_repository.py]]），没有镜像、没有
双写、第三方实体也天然进引擎。`_get_repo()` 现在用 `self.agent_id` 构造 repo
（写新实体需要 agent_id 让统一召回找得到）。

## 2026-05-27 — semantic-search 链路全删（Owner spec, scope B）

3 处直接改动：

1. **`_search_entities` 的 `search_type == "semantic"` 分支删了**。
   `auto` 已经只会路由到 `exact_id` 或 `keyword`，所以行为对调用方
   无差异——除非显式传 `search_type="semantic"`（现在会落到 `return []`
   兜底）。MCP 工具文档（[[_social_mcp_tools]]）同步删了 semantic
   选项 + 自然语言查询的示例。

2. **`_process_mentioned_entities` Stage 2 (vector similarity search) 整段
   删了**。Dedup 现在是 Stage 1 (name/alias exact match) → LLM 仲裁
   MERGE/CREATE 两步流程。后果：同人异名（"Bob" vs "Robert"）会产生
   两条 entity 记录，靠人工 / LLM 后处理合并；这是 Owner 接受的取舍
   （旧版 Stage 2 因为 mentioned-entity 从来不写 embedding 实际上一直在
   裸跑，删干净比修好它更符合 YOLO 铁律 #2）。

3. **`hook_after_event_execution` 不再调 `update_entity_embedding`**
   （行 407）。`get_embedding` import 也从文件头移除。Self-user 的描述
   依然累计更新，但不再有 embedding 副本。

# social_network_module.py — SocialNetworkModule 主体

## 为什么存在

实现 `XYZBaseModule` 合约，让 Agent 在每次对话时自动感知"对方是谁"并持续积累对对方的了解。`hook_data_gathering` 在执行前加载当前用户的实体档案并注入 `ctx_data.social_network_current_entity`；`hook_after_event_execution` 在执行后自动摘要会话内容追加到实体描述。两个 hook 配合形成了闭环的社交记忆更新流。

端口 7802，Agent-level 实例（`is_public=True`），每个 Agent 全局共享一个实例。

## 上下游关系

- **被谁用**：`HookManager` 调用两个 hook；`ModuleRunner` 通过 `create_mcp_server()` 启动 MCP 服务器；`JobInstanceService._sync_job_to_entity()` 调用 `SocialNetworkRepository.append_related_job_ids()`（不直接调用 Module，但通过 Repository 协作）
- **依赖谁**：`SocialNetworkRepository`（实体 CRUD）；`InstanceRepository`（查找自己的 instance_id）；`_entity_updater.py`（LLM 驱动的实体更新）；`_social_mcp_tools.create_social_network_mcp_server`；`prompts.SOCIAL_NETWORK_MODULE_INSTRUCTIONS`

## 设计决策

**`entity_description` 只能由 hook 写，不能由 MCP 工具写**：`extract_and_update_entity_info()` 明确拒绝更新 `entity_description` 字段（如果传入就忽略并记录 warning）。`entity_description` 是 `hook_after_event_execution` 自动积累的自然语言档案，结构化的 `identity_info`、`contact_info`、`tags` 才是 MCP 工具应该写的字段。这个分工保证了描述内容的质量不被 LLM 主动覆盖。

**`related_job_ids` 到 `ctx_data.extra_data` 的写入**：在 `hook_data_gathering` 里，如果找到了当前用户的实体且 `entity.related_job_ids` 非空，就把它写入 `ctx_data.extra_data["related_job_ids"]`。这是为了让后续的 `JobModule.hook_data_gathering`（在顺序 hook 链里 SocialNetworkModule 之后执行）能读到这份数据，加载关联 Job 的上下文。此机制依赖 `hook_data_gathering` 是顺序执行的（见 `hook_manager.py`）。

**最小实体自动创建**：`hook_after_event_execution` 发现当前 `user_id` 没有对应实体时，不跳过，而是先创建一个空的最小实体（`entity_name=user_id`，空 description，空 tags），再进行后续的摘要追加。这确保了从第一次对话开始就有记录，不需要用户主动介绍自己才开始建档。

**Persona 更新条件控制**：`_entity_updater.should_update_persona()` 决定是否要调用 LLM 推断 Persona。不是每次对话都更新——通常在交互次数达到阈值、或输出内容长度超标时才触发。这是性能权衡：Persona 推断是额外的 LLM 调用，不应该每次都做。

**模糊实体匹配作为 fallback**：`_fuzzy_find_entity()` 在精确 `entity_id` 匹配失败后，从 `ctx_data.extra_data["channel_tag"]["sender_name"]` 提取发送者姓名做关键词搜索。这是为了处理"通过外部渠道（如 Matrix）进来的消息，发送者 ID 和系统内 user_id 不一致"的情形。

## Gotcha / 边界情况

- **实例查找的懒初始化**：`_get_instance_id()` 先检查 `self.instance_id`，没有才查数据库。Module 第一次 hook 调用时会有一次数据库查询，后续调用复用缓存值。如果 Agent 的 SocialNetworkModule 实例在 hook 执行期间被删除重建，缓存会失效而不会刷新。

## 新人易踩的坑

- MCP Server 的 `create_social_network_mcp_server(port, get_mcp_db_client, SocialNetworkModule)` 传入了整个 `SocialNetworkModule` 类引用——这是为了让 MCP 工具在 MCP 进程里能实例化临时的 Module 对象查数据库，而不是持有共享实例。
- `extract_and_update_entity_info()` 是 Module 的 public API，被 `_social_mcp_tools.py` 里的 `extract_entity_info` 工具调用。两者不直接等价——MCP 工具层会先验证和格式化入参，再调用这个方法。
