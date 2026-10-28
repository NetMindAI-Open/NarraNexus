---
code_file: plugins/builtin.social_network/src/narranexus_plugins/social_network_module/_social_mcp_tools.py
last_verified: 2026-09-04
---

## 2026-08-21 — `extract_entity_info` 的 `updates` 参数说明补 `entity_name_if_new`（PR-2 增量审 Minor）

工具 docstring 的 `updates` 说明加一行:`entity_name_if_new` 是**内部调用方**用的键——仅在实体是新的或仍无名时命名,绝不覆盖已有名,不供常规使用。防止日后有人给 `updates` 加键白名单时静默过滤掉它(会让 [[inbox_recorder.py]] 首触实体又变无名)。纯文档,行为不变。
## 2026-08-10 (PR-6) — create_agent 迁走 seam，社交模块全部迁完

最后一个工具。工具只保留「铸造 `new_agent_id`（uuid4）」这一步，其余（owner 解析 +
provision + 结果整形）下沉到 [[data_access/store]]。id 作为入参传进去保证 Direct/Http
同 id → parity。**全部迁完后**：`create_social_network_mcp_server` 的 `get_db_client_fn`
形参也删了（连同 PR-5 删的 `module_class`），函数现在只收 `port`——所有工具数据访问
都走 seam，MCP server 构造零 db 依赖。

## 2026-08-10 (PR-5) — search/contact/stats 三读工具改走 seam + 清死代码

3 个读工具改为 `get_agent_data_store().search_social_network/get_contact_info/
get_agent_social_stats`。stats 的 `filter_tags` 逗号串解析留在工具层，store 收
已解析的 list。**清理**：extract/merge/delete/search/contact/stats 全迁后，
`_get_instance_and_module`（无 caller）与 `setup_mcp_llm_context`/`InstanceRepository`
import 已删（search 也不再需要——search_network 纯 repository 无 LLM）。`module_class`
形参保留（注册契约；create_agent 仍用 `get_db_client_fn`）。剩 create_agent 未迁（PR-6）。

## 2026-08-10 (PR-4) — extract/merge/delete 三写工具改走 AgentDataStore seam

三个写工具的数据访问下沉到 [[data_access/store]]：本地 DirectStore（同实例解析+
方法调用，行为不变）/ 云 HttpStore（调 PR-2 写路由，mcp 零 db 凭据）。工具体只
留 tool 层逻辑：extract 保 `updates` 的 str→dict JSON 解析 + `setup_mcp_llm_context`
（残留，方法本身不用 LLM，为 parity 保留）。`_get_instance_and_module` /
`setup_mcp_llm_context` 仍被未迁的读工具（search/contact/stats）与 create_agent 使用。
失败键仍是工具的 `message`（seam 两侧统一到它）。extract 的
`setup_mcp_llm_context` **已删**（预审二轮）：其方法纯 repository 不用 LLM，而该
setup 读 `agents` 表 + 会 raise LLMConfigNotConfigured，留着违背 seam 的「云端零
db、in-band 不抛异常」。`_get_instance_and_module`/`setup_mcp_llm_context` 仍被
search（读工具）使用。

## 2026-08-10 — merge/delete/create_agent 闭包改调共享 seam(去复制)

`merge_entities` / `delete_entity` 闭包体提炼为 [[social_network_module]] 的真
方法,闭包改调之;`create_agent` 闭包(此前是**半供给**副本,缺默认技能安装)
改调 [[provision]] `provision_new_agent`——补齐 install_defaults,不再造出
半供给 agent。三处均消除与 backend 路由的逐字复制(PR-2 pre-open review #2/#3)。

## 2026-08-01 — get_contact_info 描述加免责与改道

P1 现场模型把"问问 X 在干嘛"路由到了 `get_contact_info`——旧描述
"Use this when you need to know how to contact a specific person" 读起来
确实像。新描述明说:只给联系方式、**不联系任何人**、答不了"另一个 agent
在干什么/做完没有",要真去问用 `bus_send_to_agent`。工具描述是模型选工具
时唯一看得到的东西,所以这属于行为契约,有测试。

`agent_id` 参数描述也改成"你自己的 id"(此前"拥有此社交网络的 agent 的
ID",容易被读成可以传别人的)。

## 2026-05-27 — `search_social_network` 不再支持 `semantic`

`search_type` enum 改为 `auto | exact_id | tags | keyword`（去掉了
`semantic`）。文档增加显式说明："natural-language questions like 'who
showed purchase intent' will not match anything that does not literally
contain those words"——Agent 看到这条提示后会自然把 query 重写成
关键词形式。Example 4 (自然语言查询) 整段删了。

底层：参见 [[social_network_module.py]] 的 `_search_entities` 删除
`semantic` 分支。

# _social_mcp_tools.py — SocialNetworkModule MCP 工具定义

## 为什么存在

从 `social_network_module.py` 分离出来（2026-03-06 重构），把 MCP 工具注册逻辑与 Module 的 Hook 生命周期解耦。提供四个工具：`extract_entity_info`（主动录入实体信息）、`search_social_network`（检索联系人）、`get_contact_info`（获取联系方式）、`get_agent_social_stats`（查看 Agent 的社交概况）。

## 上下游关系

- **被谁用**：`SocialNetworkModule.create_mcp_server()` 调用 `create_social_network_mcp_server(port)` 返回 FastMCP 实例；`ModuleRunner` 部署该实例
- **依赖谁**：数据访问**全部**经 [[data_access/store]] 的 AgentDataStore seam（`get_agent_data_store()`）——本地 DirectStore 直连 repository、云端 HttpStore 调 backend `/social-network/*` 路由。工具层自己不再持有 `InstanceRepository`/`SocialNetworkModule` 类引用，也不再需要 db 客户端注入（create_agent 的 owner 查找已随 PR-6 下沉到 seam）。

## `agent_id` 如何传入

所有工具都要求显式传入 `agent_id`。MCP 工具在独立进程里没有"当前 Agent 上下文"，LLM 需要从系统提示里（`SOCIAL_NETWORK_MODULE_INSTRUCTIONS` 里的 `Your agent_id is {agent_id}` 提示）读取并传入。实例解析（`InstanceRepository.get_by_agent(agent_id, "SocialNetworkModule")` → `instance_id`）现在发生在 seam 里——DirectStore 的 `_social_module` 或 HttpStore 目标路由的 `_resolve_social_instance_id`——工具体只做参数归一化后转交 `get_agent_data_store()`。

## 设计决策

**临时 Module 实例模式**：每次数据操作都临时构造一个 `SocialNetworkModule(agent_id, database_client, instance_id)`、用完即弃（避免 MCP 进程里持有跨请求状态；`__init__` 很轻量，可接受）。这个「解析实例 + 构造临时 Module」的动作现在由 seam 承担：本地在 [[data_access/store]] 的 `DirectStore._social_module`，云端在 backend 社交路由里。工具层已不再有 `_get_instance_and_module` 辅助函数（PR-5 迁移后删除）。

**`extract_entity_info` 的标签纪律**：工具 docstring 里明确要求"每次更新最多加 2-3 个标签，多数更新加零个标签"，并提示使用规范形式（如 `expert:recommendation_system` 而不是 `expert:recommender_systems`）。这是为了控制标签膨胀——LLM 倾向于每次交互都添加新标签，最终导致一个实体有几十个噪声标签而失去检索价值。

**检索模式**：`search_social_network` 支持 `exact_id`（精确 ID）、`tags`（标签关键词）、`keyword`（LIKE 子串），默认 `auto`（自动检测：`user_`/`entity_`/`agent_` 前缀走 exact_id，否则 keyword）。向量语义检索已于 2026-05-27 退役——不再有 `semantic` 模式或 `get_embedding()` 调用，全部走 repository 关键词检索。

## Gotcha / 边界情况

- **实例解析已下沉到 seam**：数据操作工具不再自己查实例/构造 Module。`create_social_network_mcp_server` 的两个旧参数都已删除——`module_class`（曾用来打破循环导入，PR-5）和 `get_db_client_fn`（create_agent 的 owner 查找 PR-6 下沉到 seam），签名现在只剩 `port`，MCP server 构造零 db 依赖。

## 新人易踩的坑

- `get_contact_info` 工具返回的是结构化的 `contact_info` 字典（存储在 `identity_info.contact_info` 字段），不是 `entity_description` 里的自然语言描述。两者都可能包含联系方式，但格式和来源不同——前者是 Agent 主动通过 `extract_entity_info` 结构化写入的，后者是 hook 自动提炼的自然语言。

## 2026-08-18 — 工具改名映射（新增条目；上面带日期的历史条目一律不改写）

本文件上方带日期的条目里出现的是**当时**的工具名，故意保持原样 —— 镜像的价值就在于它记的是
那一天发生了什么，在带日期的条目里改名会让「什么时候变的、从什么变的」不可考。第三轮预审在
23 个文件里查出 68 处这种改写，已全部还原。

现行名字与旧名字的对应：

| 旧 | 新 |
|---|---|
| `send_message_to_user_directly` | `reply_owner`（回答刚说话的 owner）/ `notify_owner`（未被问就主动告知） |
| `bus_send_message` | `message_team` |
| `bus_send_to_agent` | `message_agent` |
| `bus_get_messages` | `read_history`（且改为按会话把手取，不再收 channel_id） |
| `bus_create_channel` | `create_team` |
| `bus_share_to_team` | `team_share_file` |
| `work_add_item` / `work_complete_item` / `work_update_status` … | `team_work_add` / `team_work_complete` / `team_work_update_status` … |
| `ChannelInboxWriter` | `InboxRecorder`（且改写自己的两张表，不再写 bus 表） |

规范解释见 [[chat_module.py]] 与 [[message_source_handler.py]] 的 2026-08-18 条目。

## 2026-09-04 · no per-module port (batch 5a)

The MCP server URL comes from `mcp_server_url("<server_name>")` (the single MCP host + `/mcp/<server_name>/sse`); the module-level port constant / `self.port` and the factory's `port` parameter are gone.
