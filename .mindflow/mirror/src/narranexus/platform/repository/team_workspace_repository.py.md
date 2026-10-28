---
code_file: src/narranexus/platform/repository/team_workspace_repository.py
last_verified: 2026-08-20
stub: false
---

# team_workspace_repository — team_files / instance_artifact_history 的数据访问

## 为什么存在

两张表原本被**三个模块各自手写 SQL** 访问：`team_files` 在 MCP 工具、staging 路径、teams 路由
各一份；`instance_artifact_history` 更糟——从 `_artifact_impl/` 里穿透
`ArtifactRepository._db`（**另一层的私有属性**）写入。

除了「每张表一个 repository」的项目约定，这里真正的作用是**把方言风险收口到一处**。散在三处
的手写 SQL 就是三次「SQLite 收、MySQL 拒」的机会——
`tests/message_bus/test_team_workspace_mysql.py` 之所以存在，正因为这在本仓库真实发生过
（见 `test_trigger_reserved_word_sql.py`）。

## 设计选择

**带裸 SQL 而不继承 `BaseRepository` 的实体机制**：两张表都没有 Pydantic 模型，消费方
（一个列表接口、一个芯片查询）读的就是普通 dict。为满足基类而造实体是没有读者的形式主义。

**wire shape 在 `list_by_team` 里选定**，不从表继承：`id` / `owner_user_id` / `content_hash`
留在服务端。`created_at` 归一为 **offset-aware** ISO——原值 UTC 但无标记，前端 `Date.parse`
会按本地时区解释，非 UTC 用户看到的时间是错的。

**`find_by_name_and_size` 刻意不带 hash 过滤**：调用方自己比对摘要，从而**只读一遍源文件**。

## 上下游

- 被 [[teams.py]]（列表 / 芯片映射 / 清理）、[[_bus_attachment_impl.py]]（去重与写入）、
  [[registration.py]]（归因追加）、[[team_files.py]]（agent 侧列举，带 bound LIMIT）使用。
  **MCP 工具那一处是本文件「为什么存在」开篇点名的三处之一**，清单漏了它就等于文档与自己的
  立论矛盾。

## 2026-08-19 — ArtifactHistoryRepository.latest_actions

artifact_id→最后动作 映射(状态块标记的数据源)。仅限有界调用方
(状态块传≤展示上限)。

## 2026-08-20 — latest_actions 只取 MAX(id) 行(#334 I10)

800 条 history 不再整段拉回取尾;双 IN-list 双倍 placeholder,状态块
≤20 上限内安全——更大的调用方要先想 placeholder 上限。
