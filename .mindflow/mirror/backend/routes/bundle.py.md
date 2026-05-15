---
code_file: backend/routes/bundle.py
last_verified: 2026-05-15
stub: false
---

## 2026-05-13 — local 多用户隔离修复

`_user_id_for_request` 改走统一 helper
`backend.auth.resolve_current_user_id`——cloud / local 共享同一条
identity 路径。`.nxbundle` 导入导出现在按真实登录用户隔离，而不是
全部塌缩到 singleton "local-default"。

# bundle.py — REST routes for `.nxbundle` export / import

## 为什么存在

把 `bundle/builder.py` + `bundle/importer.py` + `bundle/skill_backup.py` 的能力暴露成 HTTP 端点供前端使用。Bundle 是用户级别的资源动作（不是 agent 级别），所以走 `/api/bundle/*` 命名空间，独立于 `/api/agents/`。

## 上下游关系

- **被谁用**：前端 `BundleExportPage.tsx` / `BundleImportPage.tsx`
- **依赖谁**：
  - `bundle.builder.build_bundle` / `bundle.importer.preflight` / `bundle.importer.confirm`
  - `repository.SkillArchiveRepository` — list / 手动上传归档
  - `backend.auth._user_id_for_request` — 拿 user_id（local 走 `get_local_user_id`，cloud 走 `request.state.user_id`）

## 设计决策

### 端点

| Method | Path | 用途 |
|---|---|---|
| POST | /export | 流式返回 `.nxbundle` zip |
| POST | /import/preflight | 上传 zip → 解析 + 检测冲突 → return token |
| POST | /import/confirm | 用 token 真正导入 |
| GET  | /skills/archives | 列当前 user 的归档清单 |
| POST | /skills/archives/upload | 手动补归档（github URL or zip 文件） |
| POST | /export/preview/bus-channels | 列出当前 closure 候选 message-bus 频道（前端 picker 用） |
| POST | /export/preview/artifacts | 列出每个 agent 的 artifacts（Artifacts tab 用） |
| POST | /export/preview/mcps | 列出每个 agent 的 MCP URLs（Skills & MCP tab 用） |

`bus_channel_selection`（List[str]）通过 `ExportRequest` 透传到 `ExportSelection`：None = 默认走 closure 自动过滤，传值则在自动过滤的基础上再做 allowlist 限制（仍要求 owner == user 且 ≥1 closure 成员）。

`mcp_selection`（Dict[agent_id, List[mcp_id]]，2026-05-15 新增）：默认 None / {} = **一个 MCP 都不导**（opt-in，跟其他默认 default-include 的字段不一样）。MCP URL 经常指向私网/私服，所以 1.1 起强制让用户挑。

`artifact_selection`（Dict[agent_id, List[artifact_id]]，2026-05-15 新增）：默认 None = 全收。注意 artifact 的实际文件永远跟 `workspace.tar.gz` 走，这里只是过滤 DB 指针行。

### Streaming response

Export 通过 `StreamingResponse` 把 zip 文件分片写回，避免 backend 把整个文件读到内存。`iterfile()` 在生成器关闭时自动清理 tempdir。

### Preflight 跨进程稳定

参见 `bundle/importer.py` 的 mirror md（B5 修复 + scaling_assumptions §1）。

> ⚠️ **SINGLE-WORKER ASSUMPTION 链路**：preflight 落到当前 process 的本机 fs；confirm 必须命中同一台机器（看到同一份 work_dir）。多 pod 时要么共享 volume 要么改对象存储。

## Gotcha

- `import_preflight` 的 `tmpdir` cleanup 在 `finally` 里——这只清上传的 zip 文件，不清 importer 创建的 work_dir（work_dir 在 `~/.nexusagent/bundle_preflight/`）。
- `import_confirm` 失败时 work_dir 不会立即清，靠 6h TTL cleanup 兜底。
- `upload_archive` 的 sha256 在 source_type=github 模式下填 `"pending"` —— 没真正下载 tarball。设计上 export 时再 lazy download，但 v1 没实现 lazy download，所以这种行不会被 bundle export 用到。修法：让 `upload_archive` 走 github 模式时立即调 `archive_github_tarball`。
