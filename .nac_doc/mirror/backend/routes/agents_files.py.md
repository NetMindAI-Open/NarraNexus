---
code_file: backend/routes/agents_files.py
last_verified: 2026-04-10
stub: false
---

# agents_files.py — Agent 工作区文件管理路由

## 为什么存在

Agent 在执行任务时可能需要读写文件（比如保存分析结果、读取用户上传的资料）。每个 `agent_id + user_id` 组合对应一个独立的工作区目录，路径由 `xyz_agent_context.settings.base_working_path` 决定。这个路由提供前端管理工作区文件的接口：列表、上传、删除。

## 上下游关系

- **被谁用**：`backend/routes/agents.py` 聚合；前端文件管理面板
- **依赖谁**：
  - `xyz_agent_context.settings.settings.base_working_path` — 工作区根目录
  - `xyz_agent_context.utils.file_safety` — `sanitize_filename`、`ensure_within_directory`、`enforce_max_bytes`
  - `backend.config.settings.max_upload_bytes` — 上传大小限制

## 设计决策

**文件安全工具统一处理**

所有文件名都通过 `sanitize_filename` 清理（去除路径分隔符、空字节等危险字符），所有路径都通过 `ensure_within_directory` 验证（防止路径遍历攻击，如 `../../../etc/passwd`）。这两个函数集中在 `utils/file_safety.py`，不在路由里做内联字符串处理。

**上传大小在内存里检查**

文件内容被完整读入内存（`await file.read()`），然后才检查大小。这意味着大文件会消耗内存，但不会被提前拒绝。代价是：如果有人上传一个 500MB 的文件，内存会峰值增加 500MB，然后才报错。这不是流式拦截，只是事后检查。如果想在流级别拒绝，需要换 `UploadFile` 的流式读取方式。

**工作区目录惰性创建**

上传时如果工作区目录不存在，会自动 `mkdir`。这样新建的 Agent 不需要预先创建目录。

## Gotcha / 边界情况

- **文件名冲突**：上传同名文件会直接覆盖，没有版本控制或冲突提示。
- **`Bootstrap.md` 文件**：工作区里存在一个特殊文件 `Bootstrap.md`，由 `routes/auth.py` 在创建 Agent 时写入，用于标记"首次运行配置待完成"状态。这个文件会出现在文件列表里，删除它会改变 `bootstrap_active` 标志。
- **删除不校验 agent 所有权**：删除接口通过 `agent_id + user_id` 构建路径，但没有查数据库验证这个 agent 是否真的属于这个 user。路径安全由文件系统隔离（每个 agent_id_user_id 一个目录），但逻辑层的所有权校验缺失。

## 新人易踩的坑

`_get_workspace_path` 是 `{base_working_path}/{agent_id}_{user_id}` 格式，注意 agent_id 和 user_id 之间用下划线连接。如果 user_id 本身包含下划线，可能造成歧义。目前没有处理这个边界情况，因为 user_id 的格式在注册时有长度限制但没有字符限制。
