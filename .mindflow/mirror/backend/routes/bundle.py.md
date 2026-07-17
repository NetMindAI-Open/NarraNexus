---
code_file: backend/routes/bundle.py
last_verified: 2026-07-13
stub: false
---

## 2026-07-13 — include_skill_secrets passthrough

`ExportRequest.include_skill_secrets` (default False) is forwarded to `ExportSelection`. The frontend's single 'full mode' checkbox sets it together with include_channel_credentials.

## 2026-07-13 — opt-in channel credentials in the export/import routes

`POST /export` passes through `include_channel_credentials`. `/import/preflight` now returns `credential_clashes` and `/import/confirm` returns `channel_credentials_imported` / `channel_credentials_skipped_conflict`. Thin passthroughs — the logic lives in `bundle/builder.py` + `bundle/importer.py`.

## 2026-05-18 — 新增 `/import/from-url`(Template 一键 install 的入口)

承接 templates marketplace feature(`drafts/logs/template_sharing_2026_05_18.md`)。
原 import 走"用户下载 → 浏览器上传 → /preflight"两跳;新 endpoint 让后端
自己 fetch URL → 接现有 `preflight()`,实现"网站点 install → app 自动拉到
review 页"的一键体验。

**核心实现**:
- 接受 `{url, expected_sha256?}`,JWT/X-User-Id 鉴权(跟 `/import/preflight` 同款)
- URL 必须 http/https,host 必须在 `BUNDLE_FETCH_ALLOWED_HOSTS` env 白名单里。
  默认值**按 mode 分**:cloud(`settings.is_cloud_mode == True`)= `narra.nexus,www.narra.nexus`;local(sqlite,DMG / `bash run.sh`)= 上面加 `localhost,127.0.0.1,[::1]`。env 显式设置永远 override mode 默认。
  这条 mode-aware 默认值是 2026-05-18 加的,起因:DMG 内嵌 backend 跑出去拉 `http://localhost:3001/...` 被默认 allowlist 拒,UI 显示 "Could not fetch the template / load failed"——local 模式装 marketplace bundle 是 first-class 场景,默认就要允许 loopback
- httpx async stream 下载到临时文件,enforce `MAX_BUNDLE_BYTES`(复用
  `bundle/security.py`)+ `_FETCH_TIMEOUT_SEC=30s` + 不 follow redirects
- 可选 sha256 校验(`file_sha256` 复用 security.py)
- 复用 `bundle.importer.preflight(bundle_path, user_id)` —— 不重复 preflight
  那一长串逻辑(zip 解析、name clash 检测、embedding compat 等),只是给它前
  置一个"取件代办"

**安全考量(每条挡一类攻击)**:
| 控制 | 挡的是 |
|---|---|
| URL host allowlist | SSRF(Capital One 类:把后端骗去访问 `169.254.169.254/...` 拿 IAM 凭证) |
| 拒 redirect | 上游 302 → 内网/metadata IP 绕过 allowlist |
| size cap(MAX_BUNDLE_BYTES = 500 MB) | 50 GB 文件填满磁盘 |
| timeout 30s | hang server 占满连接池 |
| optional sha256 | 上游服务器被攻破后投放替换包 / URL 写错指向旧版本 |
| JWT/X-User-Id 鉴权 | 匿名调用刷流量 |

**`BUNDLE_FETCH_ALLOWED_HOSTS` env** 走 `os.environ.get` 直接读——目前
`settings.py::_DOTENV_PASSTHROUGH` 白名单还没加它(那块是 invite-code
branch 上的改动),所以本地 dev 要么把它 export 出去,要么等 invite-code
merge 后顺手加到 passthrough。生产 EC2 部署直接走 systemd/docker env,不
经 `.env`,无影响。

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
