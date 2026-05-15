---
code_file: src/xyz_agent_context/bundle/builder.py
last_verified: 2026-05-15
stub: false
---

# builder.py — bundle export pipeline

## 为什么存在

负责把"用户选定的 N 个 agent"序列化成一个自包含的 `.nxbundle` zip 文件。这个过程要做的事很多：闭包计算、过滤外部引用、剥凭证、压缩 workspace、按 install_method 处理 skill、写 artifacts 指针、计算完整性 hash、最后整体 zip 打包。集中在 builder.py 里以保证流程可审计。

## 上下游关系

- **被谁用**：
  - `backend/routes/bundle.py` — `POST /api/bundle/export` 调 `build_bundle`
- **依赖谁**：
  - 26 张表的 DB read（按 PRD §8.3 闭包过滤）
  - `bundle/security.py` — sensitive-pattern 黑名单 / sha256 / sensitive zip scan
  - `bundle/skill_backup.py` 间接（通过 `SkillArchiveRepository`）

## 设计决策

### 闭包严格 + 外部引用记 warning（议题 1+5 / §8.3）

只导用户勾选的 agent_ids，所有跨 agent 引用如果 referenced_agent_id ∉ closure 就**丢行 + 加 warning**。bundle 自包含，不外扩。

### 凭证一律剥离（议题 6）

`STRIPPED_TABLES` = `lark_credentials, user_providers, user_slots, user_quotas, users, user_password_hash` 等不进 bundle。`_scrub_user_id` 把 `created_by` / `user_id` 列替换为 `<original_owner>` 占位。

### Skill 三方法 + 现场扫描（议题 6 §8.12.6）

不依赖 `.skill_meta.json`，每次 export 都现场扫 `skills/` 真实目录 + 对照 `skill_archives` 表，让 caller 选 url / zip / full_copy。

### CPU 重活 → asyncio.to_thread

zip 打包、tar.gz 压缩、整合 sha256 都是 CPU bound。一个 100MB workspace 压缩可能几秒到十几秒。用 `asyncio.to_thread` 让 event loop 不被卡住，其他用户的 chat 不受影响。

涉及的 helper：`_zip_dir`, `_pack_workspace_sync`, `_compute_integrity_sha256`, `file_sha256` (单文件版)。

### Artifacts 粒度（2026-05-15，bundle_format 1.1）

新增 `agents/<aid>/artifacts.json` 段。DB 里 `instance_artifacts.file_path` 是相对 `settings.base_working_path` 的（开头一定是 `{aid}_{user_id}/...`），bundle 里**强制剥前缀**变成纯 workspace 相对路径。importer 端再拼上接收方的 `{new_aid}_{new_uid}/`。

> ⚠️ **关键顺序**：先剥前缀，**再**调 `_scrub_user_id`。scrub 会把 raw `user_id` 子串替换成 `<original_owner>` 占位符；如果先 scrub 再 strip，prefix 就变成 `agent_X_<original_owner>/` 匹配不上 raw 的 `agent_X_test_user/`，silent miss。

`ExportSelection.artifact_selection: Dict[agent_id, List[artifact_id]]`：None = 全收（默认，跟 social 一致）；显式 list = 白名单。文件本体永远跟着 `workspace.tar.gz` 走，所以"不勾该 artifact"只是丢 DB 指针行，文件还在。

### MCP allowlist（2026-05-15，bundle_format 1.1）

旧版本是"closure 内 owner 的 mcp_urls 全自动塞进 mcp_hints.json"，UI 没法挑。1.1 改成 opt-in：
- `ExportSelection.mcp_selection: Dict[agent_id, List[mcp_id]]`
- None / {} → 一个 mcp 都不导（默认）
- 每行 entry 现在带 `mcp_id` / `agent_id` / `is_enabled` / `metadata`，importer 拿这些直接写入接收方的 `mcp_urls`（不再只是 hint）

### Bus channel 粒度（2026-05-09）

`ExportSelection.bus_channel_selection: Optional[List[str]]`：
- `None`（默认）→ 走旧逻辑：owner==当前用户 AND ≥1 closure 成员的 channel 全收
- 传 list → 在旧逻辑基础上**再过一层 allowlist**（仍要求 owner+closure 条件）

bus_messages 跟着 channel 走（被丢的 channel 上的所有消息也被丢）。前端 `BundleExportPage` 的 "Message Bus" tab 通过 `POST /api/bundle/export/preview/bus-channels` 拿候选清单，让用户勾。Full mode 自动等价 None（全收）。

### Workspace 路径

> ⚠️ **SINGLE-WORKER ASSUMPTION**：从 `~/.nexusagent/workspaces/` 读 agent workspace。多 pod 部署需要共享 RWX volume（compose 已经做了 named volume）。详见 `.mindflow/project/references/scaling_assumptions.md` §3。

### Bundle 文件布局

按 PRD §8.1 的 "Agent-as-folder 语义树"（1.1+ 新增 `artifacts.json`）：
```
manifest.json                         (bundle_format_version: "1.1")
README.md (可选 — Bundle Notes)
agents/<agent_id>/
  agent.json
  awareness.json
  agent_messages.jsonl
  social_entities.json
  rag.json
  artifacts.json                      ← 1.1+: file_path 是 workspace-relative
  narratives/<narrative_id>/{narrative.json, events.jsonl}
  instances/<module_class>/<instance_id>.json
  workspace.tar.gz
skills/<skill_name>.zip   (zip 模式)
skills/<skill_name>-full.zip  (full_copy 模式)
mcp_hints.json                        ← 1.1+: opt-in by mcp_selection
```

## Gotcha

- 大 bundle（GB 级）会让 tmpdir 装不下。`MAX_BUNDLE_BYTES = 500MB` 强制上限，超出报错。
- `_scrub_user_id` 是浅扫描列名，**不**深入 JSON 字段值；JSON 里嵌的 user_id 不会被替换。这是 v1 简化，敏感场景要重写。
- `ExportSelection.skill_methods` dict 缺某个 skill 的条目时，那个 skill 不进 bundle —— frontend 必须保证传齐。
