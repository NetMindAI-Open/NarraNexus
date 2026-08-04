---
doc_type: reference
last_verified: 2026-08-01
scope:
  - src/xyz_agent_context/module/
  - src/xyz_agent_context/schema/module_schema.py
  - src/xyz_agent_context/schema/instance_schema.py
related_playbooks:
  - ../playbooks/add_new_module.md
---

# Module 系统参考文档

## 1. Module 系统概览

`XYZBaseModule` 是所有模块的基类，定义在 `module/base.py`。每个模块必须实现以下核心接口：

- **`get_config() -> ModuleConfig`** — 返回模块身份信息（名称、优先级、启用状态、描述、模块类型）
- **`hook_data_gathering(ctx_data: ContextData) -> ContextData`** — 在 LLM 调用前丰富上下文数据
- **`hook_after_event_execution(params: HookAfterExecutionParams)`** — LLM 执行完毕后的后处理逻辑
- **`create_mcp_server()`** — 通过 MCP 协议暴露工具给 Agent 使用
- **`get_instructions(ctx_data: ContextData)`** — 向 Agent 的 system prompt 注入模块指令

系统包含两种模块类型：

| 类型 | 特征 | 典型模块 |
|------|------|---------|
| **capability** | Agent/Narrative 创建时自动加载，始终可用 | Chat, Awareness, BasicInfo, SocialNetwork, GeminiRAG, MessageBus, Skill |
| **task** | 由 LLM 决策按需创建，拥有完整生命周期 | Job |

## 2. ModuleConfig 字段说明

```python
class ModuleConfig(BaseModel):
    name: str           # 必须与 MODULE_MAP 的 key 及类名匹配
    priority: int       # 指令排序优先级（0 最高，Awareness=0, Chat=1）
    enabled: bool       # 模块是否激活
    description: str    # 人类可读的模块用途描述
    module_type: str    # "capability" 或 "task"
```

**Instance ID 前缀**由框架从类名自动推导，无需手动指定：

- `ChatModule` -> `chat_`
- `JobModule` -> `job_`
- `LarkModule` -> `lark_`

## 3. Instance 生命周期

### 三级创建层次

| 层级 | 作用域 | 示例 | is_public | 创建时机 |
|------|--------|------|-----------|---------|
| Agent 级 | 所有用户共享 | Awareness, SocialNetwork, BasicInfo, RAG, MessageBus | true | Agent 创建时 |
| Narrative 级 | 单用户单 Narrative | ChatModule | false | Narrative 创建时 |
| Task 级 | 单次用户请求 | JobModule | false | LLM 通过 InstanceDecision 决策时 |

### 状态流转

```
ACTIVE ──[execute]──> IN_PROGRESS ──[complete/fail]──> COMPLETED / FAILED
  ^                                                         |
  └─── BLOCKED ──[deps resolve]──> ACTIVE        (unlink to history)
```

共 7 种状态：`ACTIVE`, `IN_PROGRESS`, `BLOCKED`, `COMPLETED`, `FAILED`, `CANCELLED`, `ARCHIVED`

### Instance-Narrative 绑定

Instance 与 Narrative 之间的关系存储在 `instance_narrative_links` 表中，为多对多关系。LinkType 有三种：

- **ACTIVE** — 当前正在使用的绑定
- **HISTORY** — 已完成的历史绑定
- **SHARED** — 跨 Narrative 共享的绑定

Pipeline 的 Step 2.5 在模块加载后执行链接同步。

### Capability 与 Task Instance 对比

| 维度 | Capability | Task (Job) |
|------|-----------|------------|
| 创建方式 | 自动 | LLM 决策 |
| 状态变化 | 通常保持 ACTIVE | 完整生命周期流转 |
| 依赖关系 | 无 | 可依赖其他 Job |
| 作用域 | Agent 级（public）或 Narrative 级 | 用户专属（private） |

## 4. Prompts 体系

Module 系统采用三层 Prompt 架构：

### Layer 1 — Module Instructions（模块指令）

每个模块在 `prompts.py` 中定义指令模板，使用 `{awareness}`, `{user_id}`, `{agent_name}` 等占位符。

**注入流程：**

1. `module.get_instructions(ctx_data)` 用 ContextData 的值填充模板
2. ContextRuntime 收集所有模块指令，按 `module_class` 去重
3. 按 priority 排序后拼接，放入 system prompt 的 `## Module Instructions` 区域

**各模块核心 Prompt 特征：**

- **AwarenessModule** — 5 部分结构：自我认知画像、话题组织、沟通风格、偏好检测、更新协议
- **BasicInfoModule** — Agent 身份、创建者关系、双模式沟通（Creator vs User）
- **ChatModule** — "思考 vs 说话"范式，`send_message_to_user_directly()` 是唯一能触达用户的方式
- **SocialNetworkModule** — 实体记忆规则、标签纪律（最多 3-5 个标签）、档案压缩
- **JobModule** — 任务信息、依赖关系、进度追踪、执行模板

### Layer 2 — Context Prompts（上下文提示）

在 `hook_data_gathering` 阶段生成，包括：聊天历史（近期消息）、实体摘要（社交网络）、Job 进度报告、RAG 搜索结果。这些内容注入到 `ContextData.extra_data`，格式化为 system prompt 的各个区域。

### Layer 3 — Decision Prompts（决策提示）

模块内部用于 LLM 驱动决策的提示：

- `INSTANCE_DECISION_PROMPT_TEMPLATE`（1600+ 行）— 决定创建/激活哪些 Task Instance
- Narrative 连续性检测 Prompt
- Job 分析 Prompt

**完整 Prompt 流：**

```
get_instructions() -> build_module_instructions() -> build_complete_system_prompt()
                                                          |
                                              [Module Instructions header]
                                              [Per-module instruction blocks]
                                              [Context sections from extra_data]
                                              [Chat history]
                                              [Event memory]
```

## 5. MCP 工具的 Agent 上下文传递

**架构要点：** 每个模块一个 MCP Server 进程（而非每个 Agent 一个），所有 Agent 共享同一个 Server。

**Agent 身份识别方式（2026-08-01 起）：** 工具仍声明 `agent_id` 参数（schema 不变，模型照旧传），
但**服务端注入的身份是权威来源**。平台在 per-agent 的 `mcp_servers` spec 里注入 HTTP header，
工具执行前由 `module/_mcp_identity.py` 解析：

```python
@mcp.tool()
async def skill_save_config(agent_id: str, user_id: str, skill_name: str, ...):
    # agent_id 到达这里时已被 install_caller_identity 校正为真实调用者：
    #   注入身份（权威） > 模型传的参数
    # 模型填占位符（agent_current / self / …）不再是硬失败
```

- 注入点：`context_runtime` 组装 `mcp_servers` 处（单点，两个适配器共用）
- 安装点：`XYZBaseModule.build_instrumented_mcp_server()` ← ModuleRunner 部署时调用。
  **模块无需任何改动**，声明了 `agent_id` 的工具自动获得（当前 93/93 覆盖）
- 通道是 header 而非 URL query：实测 query 在 SSE 传输上会丢失（工具调用 POST 到
  `/messages/?session_id=…`）。codex 适配器不能带任意 header，所以每个平台事实
  都借 `Authorization` 再发一份 —— bearer 是一条**位置记录**：

  ```
  Authorization: Bearer nx-agent:<agent_id>~<turn_source>~<errand_peer>~<errand_channel>
  ```

  字段数与顺序由 `_mcp_identity.BEARER_FIELDS` 钉死：位置固定、尾部空字段不
  上线（读者要容忍 1~N 段）、中间空字段合法表示未知、新事实只能**追加在末尾**、
  解析只走 `_parse_bearer`（手写 `split(sep, 1)` 会把后一段并进前一段）。
  每个字段同时有一个 `X-NarraNexus-*` 显式 header 拼写。
- 每轮注入的事实不止身份：`turn_source`（哪种轮次在调用）+ **差事作用域**
  （本轮的差事跟哪个 peer、在哪个 channel）。作用域存在的原因是「一轮」并不
  同质 —— bus 未读跨 channel 注入、提示词要求回答，所以同一轮既可能追问差事
  对手、也可能回答别的同伴；「这一条算不算差事提问」只能由知道目标的 send
  现场判断（`_message_bus_mcp_tools._send_turn_source`）。整轮盖章的复发详见
  `mirror/…/message_bus_trigger.py.md`。
- 仍然没有 thread-local；`ContextVar` 只用于读取当次 MCP 请求

> 历史：在此之前 `agent_id` **完全**由模型自填，且无任何注入。一个模型填了
> `agent_id="agent_current"` 就会撞上硬错误、进而告诉用户"做不了"——P1
> 「Agent 消极回复"我做不了"」(evt_0dcee899)。详见 `mirror/…/_mcp_identity.py.md`。

**数据库访问：** `get_mcp_db_client()` 是类级别的（同一 MCP 进程内所有 Agent 共享）。按 Agent 隔离通过查询中的 WHERE 条件实现，而非独立的数据库连接。

**安全模型：** 注入身份存在时，Server **会**用它覆盖参数里的 `agent_id`，
所以一个 agent 不能靠传别人的 id 去读别人的数据（2026-08-01 前是可以的，
当时的表述是"运行时是可信的"）。没有注入时（未来某个不带 header 的适配器、
直连 MCP 客户端）回落到旧行为：信任参数。

**端口分配：**

| 端口 | 模块 |
|------|------|
| 7801 | AwarenessModule |
| 7802 | SocialNetworkModule |
| 7803 | JobModule |
| 7804 | ChatModule |
| 7805 | GeminiRagModule |
| 7806 | SkillModule |
| 7807+ | 新增模块 |

## 6. 新建 Module 快速 Checklist

1. 创建 `module/{name}_module/` 目录
2. 实现 `XYZBaseModule` 子类，包含 `get_config()`、hooks、`create_mcp_server()`
3. 编写 `prompts.py` 定义模块指令（Layer 1）
4. 编写 `_*_mcp_tools.py` 定义工具（照旧声明 `agent_id` 参数——服务端会把它
   校正为真实调用者，见 §5，无需自己做任何身份处理）
5. 在 `module/__init__.py` 的 `MODULE_MAP` 中注册
6. 在 `schema_registry.py` 中使用 `_register(TableDef(...))` 添加数据表
7. 在 `repository/` 创建对应的数据访问类
8. 在 `schema/` 创建对应的 Pydantic 模型

## 7. 已知陷阱

- **ModuleConfig 只有 5 个字段**（name, priority, enabled, description, module_type）。Instance ID 前缀由框架从类名自动推导，不需要在 config 中手动指定。
- **MCP tools 是无状态的** — 不要在 MCP Server 进程中存储 per-agent 状态。
- **`hook_data_gathering` 必须返回 `ctx_data`**（不能返回 `None`），否则后续 pipeline 步骤会崩溃。
- **模块指令按 `module_class` 去重** — 即使存在 3 个 ChatModule Instance，指令也只出现一次。
- **capability 模块不支持依赖关系** — 只有 task（Job）模块支持 `depends_on`。
- **MCP Server 是共享进程** — 修改全局状态会影响所有 Agent，必须通过参数隔离。
