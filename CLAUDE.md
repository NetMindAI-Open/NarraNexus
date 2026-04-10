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

每个新 Module 必须完成以下全部步骤：

1. **创建模块目录**：`src/xyz_agent_context/module/{module_name}_module/`

2. **实现基类**：
```python
class NewModule(XYZBaseModule):
    @staticmethod
    def get_config() -> ModuleConfig:
        return ModuleConfig(
            module_name="new_module",
            module_prefix="new",  # Instance ID 前缀
            description="模块描述",
            version="1.0.0"
        )

    async def hook_data_gathering(self, ctx_data: ContextData) -> ContextData:
        # 数据收集逻辑
        return ctx_data

    async def hook_after_event_execution(self, params: HookAfterExecutionParams) -> None:
        # 执行后处理
        pass

    async def get_mcp_config(self) -> Optional[MCPServerConfig]:
        return MCPServerConfig(port=78XX, ...)
```

3. **注册模块**：在 `module/__init__.py` 的 `MODULE_MAP` 中添加

4. **数据库表**：
   - 创建脚本：`utils/database_table_management/create_{module}_table.py`
   - 同步脚本：`utils/database_table_management/modify_{module}_table.py`（独立脚本，不被外部引用）

5. **Repository**：在 `repository/` 创建对应的数据访问类

6. **Schema**：在 `schema/` 创建对应的 Pydantic 模型

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

- `modify_*_table.py` 是独立脚本，外部不允许引用其内容
- 新建数据表的时候，要完善 create/modify 脚本

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
