## 铁律

以下规则在所有阶段（设计、计划、实现、审查）无条件生效，不可绕过：

1. **用用户的语言回复**，所有文档用用户给你的语言写，但是代码中不允许出现中文，都用英文
2. **不做任何向后兼容**——项目尚未完善，兼容会带来不必要的麻烦，YOLO！你要大胆去做、去设计，不畏艰难
3. **模块独立、热插拔**——Module 之间不互相引用，不互相依赖
4. **通用逻辑与场景特定逻辑分离**——Prompt 和判断逻辑只包含通用规则，不写死具体场景示例（如销售场景）。具体场景由 Agent 在 Awareness 中定义
5. **不只治标，要治本**——修 bug 时要追问根因，不怕改动大，只要结果更优雅、更高效就值得
6. **数据库不做危险变更**——不做类型缩窄、类型彻底变更等操作
7. **双运行方式对齐**——`bash run.sh` 和桌面端 dmg 的运行逻辑必须一致，改一个就要检查另一个
8. **不要让代码变成屎山**——每做一个功能，全面检查有没有相关代码也需要调整
9. **不强依赖某一个 Agent 框架或 LLM**: 我们不能完全的依赖某一个 Agent 框架，或者 LLM，所以设计的时候要考虑好，不能有某一个环节完全必须用某一个 Agent 框架，后续不能换。
10. **Tier-2 文档同步**——对 `.py/.tsx/.ts/.rs` 做行为性修改时，必须重读对应 `.nac_doc/mirror/…/X.md`，若修改让 intent 失效，同一 commit 内更新 md 并刷新 frontmatter 的 `last_verified`。新增代码文件 → 同一 commit 新增对应 mirror md；删除代码文件 → 同一 commit 删除对应 mirror md。**新增/修改前必须先读对应 mirror md**。

---

## 三级文档体系

本项目使用 NAC Doc 三级文档体系：

1. **Tier-1 · 代码内**：行间注释、docstring、文件头
2. **Tier-2 · `.nac_doc/mirror/`**：镜像源代码结构。每个代码文件对应一个 md，写 intent（为什么存在、上下游、设计决策、gotcha）。**不**写签名/做了什么。
3. **Tier-3 · `.nac_doc/project/`**：`references/`（深度权威）+ `playbooks/`（任务 SOP）

详细方法论见 `.nac_doc/README.md`。NexusAgent 专属入口见 `.nac_doc/_overview.md`。

## 工作流启动

接到用户任务后，在 brainstorm / 写代码之前**必做**：

1. **扫深度文档索引**：看任务是否匹配下方「深度文档索引」中某个 playbook 或 reference 的「何时读」触发器
2. **命中则先读**：匹配的 playbook/reference 必须在动手前 Read 一遍，把 SOP 纳入计划
3. **编辑代码前**：对要改的 `.py/.tsx/.ts/.rs` 文件，Read 对应 `.nac_doc/mirror/…/X.md` 理解 intent，再动手
4. **完工时**：遵守铁律 #10，同步对应 mirror md

## 深度文档索引

> 本节是 tier-3 文档的门面。每条都带「何时读」触发器 —— 匹配即 Read，不是参考而是**必读**。

### References（权威深度文档 · 按需读取）

- `.nac_doc/project/references/architecture.md` — 架构分层全貌
  **何时读**：跨层重构、需要理解依赖方向时
- `.nac_doc/project/references/agent_runtime_pipeline.md` — 7 步流水线详解
  **何时读**：修改 `agent_runtime/` 下任何 step，或 debug 流水线问题
- `.nac_doc/project/references/module_system.md` — Module 基类、Hook、MCP
  **何时读**：修改 Module 基类、Hook 行为、Instance 生命周期
- `.nac_doc/project/references/narrative_system.md` — Narrative 编排
  **何时读**：修改 Narrative 选择/去重/向量匹配逻辑
- `.nac_doc/project/references/context_engineering.md` — Context 构建引擎
  **何时读**：修改 ContextData、Prompt 装配
- `.nac_doc/project/references/database_schema.md` — 所有表结构
  **何时读**：改表、加表、或遇到字段语义不清
- `.nac_doc/project/references/coding_standards.md` — 完整编码规范
  **何时读**：做 code review、或不确定命名/结构约定时
- `.nac_doc/project/references/frontend_architecture.md` — 前端结构
  **何时读**：改前端状态管理、路由、API 调用层
- `.nac_doc/project/references/desktop_tauri_integration.md` — Tauri sidecar
  **何时读**：改 `run.sh` 或 Tauri sidecar，触发铁律 #7
- `.nac_doc/project/references/llm_and_framework_abstraction.md` — 框架抽象层
  **何时读**：新增 LLM provider、Agent 框架适配

### Playbooks（任务 SOP · 匹配即读）

- `.nac_doc/project/playbooks/onboarding.md` — Day-1 新人入职
  **何时读**：首次接触本项目
- `.nac_doc/project/playbooks/add_new_module.md` — 新建 Module 端到端
  **何时读**：用户说「加一个 Module」、「新建模块」—— **必须先读再动手**
- `.nac_doc/project/playbooks/add_new_database_table.md` — 新建数据表
  **何时读**：加新表、改表结构 —— **必须先读再动手**
- `.nac_doc/project/playbooks/add_new_api_endpoint.md` — 后端 + 前端联动加 API
  **何时读**：加新 API endpoint
- `.nac_doc/project/playbooks/add_new_frontend_page.md` — 加前端页面
  **何时读**：加新前端页面
- `.nac_doc/project/playbooks/debug_runtime.md` — 流水线 debug 套路
  **何时读**：runtime 报错或行为异常
- `.nac_doc/project/playbooks/run_tests.md` — TDD 工作流
  **何时读**：写测试前
- `.nac_doc/project/playbooks/handle_migration.md` — 数据库迁移
  **何时读**：需要改已有表结构（触发铁律 #6）
- `.nac_doc/project/playbooks/write_nac_doc.md` — 写 tier-2 md
  **何时读**：首次给某个文件写 mirror md、或 stub 转成稿时
- `.nac_doc/project/playbooks/work_with_worktree.md` — worktree 流程
  **何时读**：开始多人并行任务、或按 superpowers 流程启动 plan

> **未写就时的 fallback**（Phase 2 内容创作工作延后）：Read 返回 file-not-found 时，按以下顺序回退——
>
> 1. **先读 CLAUDE.md 本文**：`项目介绍`、`架构分层`、`新建 Module 步骤`（简表版）、`编码规范` 这四节合起来覆盖了绝大部分 on-board 信息
> 2. **再读对应的 mirror md**：`.nac_doc/mirror/<path>.md` —— 即使是 stub，frontmatter 的 `code_file` 也会告诉你去读哪个源码文件
> 3. **最后读源码**：`code_file` 指向的 `.py/.tsx/.ts/.rs`，配合 docstring 和文件头注释（铁律 #1 保证都是英文）
> 4. **完工时回填 mirror md**：按铁律 #10，在同一 commit 内把你理解的 intent 写进对应 mirror md，把 frontmatter 的 `stub: true` 改成 `false`，刷新 `last_verified`
>
> `.nac_doc/README.md` 是**方法论**，教你 HOW 写 md，不是项目知识；它**无法**替代项目特定信息。

---

## Superpowers 集成

### 覆盖 Superpowers 默认行为

- **设计文档位置**：`reference/self_notebook/specs/YYYY-MM-DD-<topic>-design.md`（覆盖 Superpowers 默认的 `docs/superpowers/specs/`）
- **计划文档位置**：`reference/self_notebook/plans/YYYY-MM-DD-<topic>.md`（覆盖 Superpowers 默认的 `docs/superpowers/plans/`）
- **使用 git worktree**——遵循 Superpowers 的 `using-git-worktrees` skill，worktree 目录使用 `.worktrees/`
- **强制 TDD**——遵循 Superpowers 的 `test-driven-development` skill，所有新功能和 bug 修复必须先写测试
- **待修问题记录**：发现的问题如果暂不处理，记录到 `reference/self_notebook/todo/` 目录

### brainstorming 阶段必须考虑

设计任何新功能时，必须在方案中回答以下问题：

1. **涉及哪些层？** 对照架构分层（见下文），明确每一层需要什么变更
2. **需要新建 Module 吗？** 如果是，必须遵循"新建 Module 步骤"（见下文）
3. **数据表变更？** 需要哪些新表/字段，create 和 modify 脚本是否都覆盖到
4. **前端联动？** 每做完一个新功能，必须给出前端展示建议并询问用户是否采纳
5. **对现有模块的影响？** 检查是否有现有代码需要同步调整

### subagent implementer 必须遵循

- 遵循下文的命名规范、注释规范、数据库操作规范
- 新文件必须包含文件头注释
- 私有实现放 `_*_impl/` 目录，不对外导出
- Repository 放 `repository/`，不放在 module 内部
- Schema 放 `schema/`，集中管理

---

## 项目介绍

开发一个拥有长期记忆（Narrative）、Module 可热插拔的 Agent 系统。核心是算法与 Agent 的开发。前端和后端同样重要——用户体验直接影响产品价值。

---

## 架构分层

```
API Layer (FastAPI Routes)        ← 控制层
AgentRuntime (Orchestrator)       ← 编排层（7步流水线）
Services (Narrative, Module)      ← 服务协议层
Implementation (_*_impl/)         ← 私有实现层
Background Services (services/)   ← 后台服务层（ModulePoller）
Repository (Data Access)          ← 数据访问层
AsyncDatabaseClient + Schema      ← 数据层
```

| 层级 | 目录 | 职责 |
|------|------|------|
| Schema | `schema/` | Pydantic 数据模型定义 |
| Repository | `repository/` | 纯数据库 CRUD，继承 BaseRepository |
| 服务协议层 | `*_service.py` | 对外暴露统一接口 |
| 实现层 | `_*_impl/` | 具体业务逻辑，私有不导出 |
| 后台服务层 | `services/` | 后台轮询服务（ModulePoller, InstanceSyncService） |
| 编排层 | `agent_runtime/` | 流程协调，调用各 Service |
| API 层 | `backend/routes/` | HTTP/WebSocket 端点（独立于核心包） |

### 设计模式

| 模式 | 应用位置 | 说明 |
|------|---------|------|
| 依赖注入 | AgentRuntime | 接受 LoggingService, ResponseProcessor, HookManager |
| Repository 模式 | `repository/` | BaseRepository 泛型基类，解决 N+1 问题 |
| 服务协议层 + Bridge | NarrativeService, ModuleService | 对外统一接口，委托 `_*_impl/` 实现 |
| 工厂/单例 | `db_factory.py` | 全局单例 AsyncDatabaseClient |
| Hook 模式 | `module/base.py` | 生命周期钩子：`hook_data_gathering`, `hook_after_event_execution` |

---

## 新建 Module 步骤

→ 详细端到端流程见 `.nac_doc/project/playbooks/add_new_module.md`

必须对齐的铁律：
- 模块必须继承 `XYZBaseModule` 并定义 `get_config()`
- 必须在 `module/__init__.py` 的 `MODULE_MAP` 中注册
- 数据库表在 `utils/schema_registry.py` 中用 `_register(TableDef(...))` 注册（**不再**使用 `create_*_table.py` / `modify_*_table.py`）
- Repository 放 `repository/`；Schema 放 `schema/`；私有实现放 `_{module}_impl/`
- MCP 端口从下表选下一个可用值

### MCP 端口分配

| 端口 | Module |
|------|--------|
| 7801 | AwarenessModule |
| 7802 | SocialNetworkModule |
| 7803 | JobModule |
| 7804 | ChatModule |
| 7805 | GeminiRagModule |
| 7806 | SkillModule |
| 7807+ | 新 Module 从这里顺序分配 |

### 数据库表注册（schema_registry）

所有表统一在 `utils/schema_registry.py` 中注册，SQLite 和 MySQL 共用同一份定义。每列同时声明两种方言的类型：

```python
from xyz_agent_context.utils.schema_registry import _register, TableDef, Column, Index

_register(TableDef(
    name="instance_lark_bindings",
    columns=[
        Column("id", "INTEGER", "BIGINT UNSIGNED", nullable=False, primary_key=True, auto_increment=True),
        Column("instance_id", "TEXT", "VARCHAR(128)", nullable=False, unique=True),
        Column("config_json", "TEXT", "MEDIUMTEXT"),
        Column("created_at", "TEXT", "DATETIME(6)", nullable=False, default="(datetime('now'))"),
        Column("updated_at", "TEXT", "DATETIME(6)", nullable=False, default="(datetime('now'))"),
    ],
    indexes=[Index("idx_lark_bindings_instance", ["instance_id"], unique=True)],
))
```

关键规则：
- `sqlite_type` + `mysql_type` **必须同时填**，`auto_migrate()` 按 backend dialect 自动选
- 时间戳统一用 `default="(datetime('now'))""`，MySQL DDL 生成时自动翻译为 `CURRENT_TIMESTAMP(6)`
- **不需要**手写 CREATE TABLE / ALTER TABLE —— `auto_migrate()` 在每个进程启动时幂等执行，自动建表、加列、加索引
- 表名约定：Module 专属表以 `instance_` 前缀开头（如 `instance_jobs`, `instance_social_entities`, `instance_lark_bindings`）

---

## 编码规范

### 命名

| 类型 | 规范 | 示例 |
|------|------|------|
| 类名 | PascalCase | `AgentRuntime`, `NarrativeService`, `ChatModule` |
| 函数/方法 | snake_case | `hook_data_gathering`, `get_by_id` |
| 变量 | snake_case | `agent_id`, `user_id`, `ctx_data` |
| 常量 | UPPER_SNAKE_CASE | `MODULE_MAP`, `MAX_NARRATIVES_IN_CONTEXT` |
| 私有包 | `_` 前缀 | `_agent_runtime_steps/`, `_module_impl/` |
| ID 生成 | 前缀 + 8位随机 | `evt_a1b2c3d4`, `nar_e5f6g7h8` |

### 文件头

```python
"""
@file_name: xxx.py
@author: 
@date: 20xx-xx-xx
@description: 核心功能描述

详细说明...
"""
```

### docstring

```python
async def select(self, agent_id: str) -> Tuple[List[Narrative], Optional[List[float]]]:
    """
    选择合适的 Narratives

    工作流程：
    1. 检测话题连续性
    2. 向量匹配或创建新 Narrative

    Args:
        agent_id: Agent ID

    Returns:
        (Narrative 列表, query_embedding)
    """
```

### 数据库操作

```python
# AsyncDatabaseClient
row = await db.get_one("table", {"id": "xxx"})
rows = await db.get_by_ids("table", "id", ["id1", "id2"])
await db.insert("table", data)
await db.update("table", filters, data)
await db.delete("table", filters)

# Repository 模式
class EventRepository(BaseRepository[Event]):
    table_name = "events"
    id_field = "event_id"

    def _row_to_entity(self, row) -> Event:
        return Event(**row)

    def _entity_to_row(self, entity) -> Dict:
        return entity.model_dump()
```

---

## 易忘事项

- 数据库表定义统一在 `utils/schema_registry.py`，**不再**有独立的 `create_*_table.py` / `modify_*_table.py` 脚本
- 新建数据表时，在 `schema_registry.py` 添加 `_register(TableDef(...))`，`auto_migrate()` 会在下次启动时自动生效
- `Column` 的 `sqlite_type` 和 `mysql_type` **必须同时填写**

---

## 项目命令参考

完整命令见 `Makefile`（`make help` 查看所有可用命令）。

### 启动服务（4 个进程，各需独立终端）

| 进程 | 命令 | 说明 |
|------|------|------|
| FastAPI 后端 | `make dev-backend` | API 服务，端口 8000 |
| MCP 服务器 | `make dev-mcp` | Module 的 MCP tool 服务 |
| ModulePoller | `make dev-poller` | 检测 Instance 完成并触发依赖链 |
| 前端 | `make dev-frontend` | Vite 开发服务器 |

### 数据库

| 命令 | 说明 |
|------|------|
| `make db-sync-dry` | 预览表结构变更 |
| `make db-sync` | 执行表结构同步 |

### 质量检查

| 命令 | 说明 |
|------|------|
| `make lint` | Ruff（后端）+ ESLint（前端） |
| `make typecheck` | Pyright（后端）+ tsc（前端） |
| `make test` | 运行 pytest |

---

## 目录结构参考

```
NexusAgent/
├── .nac_doc/                      # NAC Doc 三级文档系统
│   ├── README.md                  #   方法论全本（Skill 种子）
│   ├── _overview.md               #   NexusAgent 顶层入口
│   ├── mirror/                    #   Tier-2：代码镜像 intent
│   └── project/                   #   Tier-3：references + playbooks
│
├── scripts/
│   ├── nac_doc_lib.py             #   NAC Doc 共享库
│   ├── scaffold_nac_doc.py        #   Phase 1 stub 生成
│   ├── check_nac_doc.py           #   Layer 1 结构不变量检查
│   ├── audit_nac_doc.py           #   Layer 3 软腐烂审计
│   └── install_git_hooks.sh       #   pre-commit hook 安装
│
├── backend/                       # FastAPI 后端
│   ├── main.py                    # 应用入口
│   └── routes/                    # 路由定义
│
├── frontend/                      # React 前端
│   └── src/
│       ├── components/            # UI 组件
│       ├── stores/                # Zustand 状态管理
│       ├── hooks/                 # React Hooks
│       ├── lib/                   # 工具库
│       └── types/                 # TypeScript 类型
│
├── src/xyz_agent_context/         # 核心包
│   ├── agent_runtime/             # 编排层
│   ├── agent_framework/           # LLM SDK 适配层
│   ├── context_runtime/           # 上下文构建引擎
│   ├── narrative/                 # Narrative 编排系统
│   ├── module/                    # 功能模块系统
│   │   ├── base.py                # XYZBaseModule 基类
│   │   ├── module_service.py      # 模块服务协议层
│   │   ├── hook_manager.py        # Hook 生命周期管理
│   │   ├── module_runner.py       # MCP 服务器部署
│   │   ├── _module_impl/          # 私有实现
│   │   ├── awareness_module/
│   │   ├── basic_info_module/
│   │   ├── chat_module/
│   │   ├── social_network_module/
│   │   ├── job_module/
│   │   └── gemini_rag_module/
│   │
│   ├── schema/                    # Pydantic 数据模型
│   ├── repository/                # 数据访问层
│   ├── services/                  # 后台服务
│   └── utils/                     # 工具类库
│       └── database_table_management/
│
└── pyproject.toml
```
