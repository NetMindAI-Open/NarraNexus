---
code_file: plugins/builtin.social_network/src/narranexus_plugins/social_network_module/social_network_module.py
last_verified: 2026-09-04
---

## 2026-08-25 — hook 里接住「失败」与「空结果」的区分

[[_entity_updater.py]] 把 `summarize_new_entity_info` / `infer_persona` 改成
返回 `Optional[str]`（`None` = 调用失败），本文件是唯一调用方，负责把这个
区分**用起来**：

- `new_summary is None` → 记一条 warning 并跳过描述写入（失败已在下游上报），
  与 `""`（LLM 跑通了但没找到值得记的东西）分开处理。原来两者都是 `""`，
  于是一把过期的 helper key 和一次平淡的对话在日志里长得一模一样。
- `new_persona` 为 `None` 时不回写。原实现失败时返回**当前** persona 再原样
  写回，一次 no-op 写入和一次成功刷新无从分辨。

**调用方自己这一侧也不再静默吞（PR#360 review 补）**：主实体创建失败
（`create_primary_entity`——最重的一处，建不出来则本回合 summary / 计数 /
persona 全部不发生，此前零证据）和 `_process_mentioned_entities` 的逐实体
失败（`process_mentioned_entity`——覆盖第三方实体创建、tags/aliases 合并，
以及 Stage-1 的**读**失败：读失败会让每个被提及实体都判为全新，每回合新建
重复节点）现在都留审计行。

顺带修掉一个错标签：dedup 管线的外层 except 打的是
`Batch entity extraction failed`，是从抽取那条 handler 复制来的，会把排查
直接引到错误的 LLM 调用上。以及同一个 traceback 被 `logger.exception` 连打
两次。

**测试在 [[test_memory_write_audit_wiring.py]]**，注意它 patch 的是**本模块**
的 `_report_write_failure` 绑定——本文件是模块顶层 import，patch
`_entity_updater` 的同名属性对这两处无效，断言会对着空列表静静通过。

`entity_id_candidate` 的赋值提到了 `try` **外**：except 里要用它上报，留在
try 内第一行等于让错误路径依赖它自己没出错（第一轮迭代会从 except 里抛
`NameError` 连带丢掉本批剩余实体；后续迭代会把失败记到上一个实体头上）。

这些 except **必须继续不抛**——hook 挂掉会连带影响 Step-6 回调。

五个调用点都显式传 `agent_id=self.agent_id`——下游的 owner 告警要靠它反查
`agents.created_by`。预审时把这些参数的默认值去掉了（铁律 #2）：留着默认值
等于给「忘记传」开一条静默降级的路，而那条路会正好抵消这次改动的全部价值。

## 2026-08-21 — `extract_and_update_entity_info` 支持 create-only 名字键(PR-2 预审 Important)

新增 `entity_name_if_new` 键:**create 分支**用它作 `entity_name` 兜底(`entity_name` 显式优先);**merge/existing 分支**是 **fill-if-empty**——`_name=updates.pop("entity_name_if_new"); if _name and not (existing.entity_name or "").strip(): updates["entity_name"]=_name`(增量审 Minor:也给「别的路径建出来的无名实体」补名,但非空名**绝不覆盖**)。用途:[[inbox_recorder.py]] 自动记 reach 时给陌生发件人命名(否则无名,§3b 按名字搜不到),又不覆盖 LLM 规范名。守卫 `test_inbox_reach_recording.py`(create 带名 / 已存在无名被填 / 非空名不覆盖三面)。

## 2026-08-18 — create_agent 的拒绝**规则**也上提了,不只是那两句话

新增 `create_agent_text_reject(agent_name, agent_description) -> Optional[str]`
与 `default_created_by_description(author)`,两条腿([[social_network.py]] 路由 +
[[store.py]] DirectStore)都调它们。

**为什么不能只共享常量**:上一版把 `CREATE_AGENT_EMPTY_NAME_MSG` /
`CREATE_AGENT_TEXT_TOO_LONG_MSG` 提成了共享串,但**做判断的那段代码是两份** ——
归一、空名判、长度判、以及「长度必须在空名之后」这条顺序不变量,靠两边各写一句
注释说「和另一边保持一致」。给 `agent_name` 再加一条规则(比如拒换行/控制字符 ——
它是行标题,这需求很现实)只改一条腿,就会重新裂开成「同一次工具调用两句话」,
而这正是本轮要消灭的形态。成功那一半(`format_create_agent_success`)早就是共享
函数了,失败这一半当时没跟上。

**顺序落在函数体里**,不拆成两个可分别调用的 predicate —— 拆了等于没上提。
`" " * 300` 归一后是空名,不是超长名;反了就会给出另一句。

**返回字符串而不是响应 dict**:路由用 `error` 键、DirectStore 用 `message` 键
(由 [[store.py]] 的 `_write_message_key` 折回),形状属于各条腿,只有措辞是共享的。
把 dict 塞进共享函数会破坏那层折叠。

**归一留在调用方**:归一后的值两条腿还要继续用(写库 + 回执 + 日志),只有「判」
搬进来了。

`default_created_by_description` 走**截断**而非拒绝:它是这条路上唯一在所有检查
之后才拼出来的值(17 + 创建者名字),而拒绝一个调用方从没输入过的串没法解释。

测试:`tests/backend/test_create_agent_empty_name_parity.py` —— 逐条腿的用例证明
两条腿一致,表驱动用例证明它们一致在**什么规则**上,含那条只在一种输入上体现的
顺序不变量(空名 + 超长描述 → 必须回空名那句)。

## 2026-08-17 — 新增 CREATE_AGENT_EMPTY_NAME_MSG

与 `CREATE_AGENT_NO_OWNER_MSG` 并排的第二个共享串:两条建 agent 路径
(DirectStore [[store.py]] 与 [[social_network.py]] 路由)拒绝空名时必须逐字节
同串 —— 与那条注释里写的 byte-parity 理由完全一样。

为什么建 agent 也要拒空名:没有名字的 agent 在所有界面退回显示裸 `agent_id`,
纯空格的更糟(名字 truthy,连 `agent_id` 兜底都不触发,行标题直接空白)。
改名两条路径([[_awareness_writes]] 与 [[auth.py]])本来就拒,而创建侧一直收 ——
同一个值**可以创建、不可以改成**,这是本轮修掉的不对称之一。

## 2026-08-10 (PR-6) — 新增 `format_create_agent_success` + `CREATE_AGENT_NO_OWNER_MSG`

create_agent 的成功 dict（含 warnings 上浮）与无 owner 文案的唯一来源，seam 的
DirectStore 与 create-agent 路由都 import 用——给定同 (agent_name, new_agent_id)
两侧输出逐字相同（new_agent_id 是工具铸造后传入的入参，非各自随机生成）。经包
`__init__` re-export。

## 2026-08-10 (PR-5) — 新增共享结果整形器 `format_contact_result` / `format_stats_result`

两个纯函数把 `recall_entity_info` / `get_agent_stats` 的原始结果整形成
get_contact_info / get_agent_social_stats 工具的返回 dict。seam 的 DirectStore
与 backend `/social-network/{contact,stats}` 路由都 import 用，工具的表现逻辑
只此一份、不再被抄进路由（与 [[store]] 的 parity 目标一致）。经包 `__init__` re-export。

## 2026-08-10 (PR-4) — 新增共享 `social_instance_not_found_msg`

模块级纯函数，"agent 无 SocialNetworkModule 实例"的**唯一**文案源。[[store]] 的
DirectStore 与 backend [[social_network]] 写路由（HttpStore 路径）都 import 它，
保证 seam 两侧这条边界返回逐字相同（否则迁移的写工具在实例缺失时分叉）。措辞沿用
本文件姊妹 GET 路由的 "... for agent: X"。经包 `__init__` re-export。

## 2026-08-10 — 新增 merge_entities / delete_entity 方法(供 MCP+路由共用)

把原本散在 `_social_mcp_tools.py` 闭包与 backend 路由里的 merge/delete 业务
逻辑(tags 并集去重、identity/contact 深合、related_job 并集、描述追加、
交互计数求和、保留最新交互时间)提炼为本类的真方法,与既有
`extract_and_update_entity_info` 同层。MCP 闭包与 `backend/routes/agents/
social_network.py` 路由都改调这两个方法,消除两份复制、堵住 drift(PR-2
pre-open review #2)。方法接纯数据参数,内部自解析 instance/repo。

## 2026-07-28 — R4b：实体卡（§5）搬进 contribute_turn_context

（本条为 R4 系列在新 dev 结构上的重放；原始实现 2026-07-25 于 feat/cli-session-capture 分支，该历史不在本分支 mirror 中，条目自含。）

- `__init__` 现在把 agent_id 同时烘焙进 legacy 与 stable 两个模板
  （`self._instructions_legacy` / `self._instructions_stable`；
  `self.instructions` 初始化为 legacy 供 functional_information 用）。
- `contribute_instructions` override：按
  `settings.prompt_turn_context_relocation_enabled` 选模板后走基类 format
  路径（`{{...}}` 转义行为两条路径一致）。关 = legacy 逐字节一致。
- `contribute_turn_context`：返回 `##### Current User Information\n` +
  `ctx_data.social_network_current_entity`。hook 的三种 fallback 文案
  （首次见面 / 无 user 上下文 / 加载失败）与错误文案（含异常串——正是最
  不能进 system prompt 的易变内容）都走这条通道；hook 本身一行未动。
  字段缺失 → ""（fail-open）。

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

3. **`after_turn` 不再调 `update_entity_embedding`**
   （行 407）。`get_embedding` import 也从文件头移除。Self-user 的描述
   依然累计更新，但不再有 embedding 副本。

# social_network_module.py — SocialNetworkModule 主体

## 为什么存在

实现 `XYZBaseModule` 合约，让 Agent 在每次对话时自动感知"对方是谁"并持续积累对对方的了解。`gather` 在执行前加载当前用户的实体档案并注入 `ctx_data.social_network_current_entity`；`after_turn` 在执行后自动摘要会话内容追加到实体描述。两个 hook 配合形成了闭环的社交记忆更新流。

端口 7802，Agent-level 实例（`is_public=True`），每个 Agent 全局共享一个实例。

## 上下游关系

- **被谁用**：`HookManager` 调用两个 hook；`ModuleRunner` 通过 `create_mcp_server()` 启动 MCP 服务器；`JobInstanceService._sync_job_to_entity()` 调用 `SocialNetworkRepository.append_related_job_ids()`（不直接调用 Module，但通过 Repository 协作）
- **依赖谁**：`SocialNetworkRepository`（实体 CRUD）；`InstanceRepository`（查找自己的 instance_id）；`_entity_updater.py`（LLM 驱动的实体更新）；`_social_mcp_tools.create_social_network_mcp_server`；`prompts.SOCIAL_NETWORK_MODULE_INSTRUCTIONS`

## 设计决策

**`entity_description` 只能由 hook 写，不能由 MCP 工具写**：`extract_and_update_entity_info()` 明确拒绝更新 `entity_description` 字段（如果传入就忽略并记录 warning）。`entity_description` 是 `after_turn` 自动积累的自然语言档案，结构化的 `identity_info`、`contact_info`、`tags` 才是 MCP 工具应该写的字段。这个分工保证了描述内容的质量不被 LLM 主动覆盖。

**`related_job_ids` 到 `ctx_data.extra_data` 的写入**：在 `gather` 里，如果找到了当前用户的实体且 `entity.related_job_ids` 非空，就把它写入 `ctx_data.extra_data["related_job_ids"]`。这是为了让后续的 `JobModule.gather`（在顺序 hook 链里 SocialNetworkModule 之后执行）能读到这份数据，加载关联 Job 的上下文。此机制依赖 `gather` 是顺序执行的（见 `hook_manager.py`）。

**最小实体自动创建**：`after_turn` 发现当前 `user_id` 没有对应实体时，不跳过，而是先创建一个空的最小实体（`entity_name=user_id`，空 description，空 tags），再进行后续的摘要追加。这确保了从第一次对话开始就有记录，不需要用户主动介绍自己才开始建档。

**Persona 更新条件控制**：`_entity_updater.should_update_persona()` 决定是否要调用 LLM 推断 Persona。不是每次对话都更新——通常在交互次数达到阈值、或输出内容长度超标时才触发。这是性能权衡：Persona 推断是额外的 LLM 调用，不应该每次都做。

**模糊实体匹配作为 fallback**：`_fuzzy_find_entity()` 在精确 `entity_id` 匹配失败后，从 `ctx_data.extra_data["channel_tag"]["sender_name"]` 提取发送者姓名做关键词搜索。这是为了处理"通过外部渠道（如 Matrix）进来的消息，发送者 ID 和系统内 user_id 不一致"的情形。

## Gotcha / 边界情况

- **实例查找的懒初始化**：`_get_instance_id()` 先检查 `self.instance_id`，没有才查数据库。Module 第一次 hook 调用时会有一次数据库查询，后续调用复用缓存值。如果 Agent 的 SocialNetworkModule 实例在 hook 执行期间被删除重建，缓存会失效而不会刷新。

## 新人易踩的坑

- MCP Server 由 `create_social_network_mcp_server(port)` 构造（单参）。数据操作工具通过 [[data_access/store]] 的 seam 解析实例并临时构造 Module（本地 DirectStore / 云端 backend 路由），工具层不再接收 `SocialNetworkModule` 类引用或 db 客户端注入（旧 `module_class` PR-5 删、`get_db_client_fn` PR-6 删）。
- `extract_and_update_entity_info()` / `merge_entities()` / `delete_entity()` / `search_network()` / `recall_entity_info()` / `get_agent_stats()` 是 Module 的 public API，被 seam（DirectStore + backend 社交路由）调用。`get_agent_stats` 曾是私有 `_get_agent_stats`，PR-5 因成为跨包（backend 路由）契约而提升为公开命名。

## 2026-09-04 · no per-module port (batch 5a)

The MCP server URL comes from `mcp_server_url("<server_name>")` (the single MCP host + `/mcp/<server_name>/sse`); the module-level port constant / `self.port` and the factory's `port` parameter are gone.

## 2026-09-04 · declares its platform metadata (batch 5b)

`get_config()` is static and carries what the platform used to table about this module (default / base / always-load membership, instance prefix, role, display, decision metadata as applicable).

## 2026-09-04 · declares its agent-level instance (batch 5b.2)

`agent_instance` in `get_config()` (description / keywords / topic hint) replaces the factory's or descriptor's copy; the InstanceFactory creates it for every agent.
