---
code_file: src/xyz_agent_context/bundle/importer.py
last_verified: 2026-06-09
stub: false
---

## 2026-06-09 — import backfills the unified-memory search indexes

`import_bundle` raw-inserts operational rows, which bypasses the live
projection-write points (crud._index_narrative / step_4 interaction /
create_job / send_message), so an imported narrative / job / bus message /
interaction was invisible to `remember` until re-touched. A final pass calls
`backfill_agent_search_indexes` (now in the shared [[backfill]] module — 2026-06-09
it was extracted out of importer so the versioned migration could reuse the exact
same logic) per freshly-imported agent, re-projecting `narratives` /
`instance_jobs` / `bus_messages` / `events` into `memory_<kind>` with the same
searchable text + source_ref each live writer produces. entity is already rebuilt
via `save_entity` during import; observation is LLM-derived (never in a bundle)
and unrecoverable — both out of scope. Best-effort + per-agent isolation,
idempotent. Covers BOTH old bundles (which predate the indexes) and current ones
(same raw-insert path). Scoped to THIS import — `new_agent_ids` are freshly
minted. Counter: `written_summary['search_indexes_backfilled']`. Tests:
`tests/bundle/test_import_backfill.py` + a wiring assert in `test_roundtrip.py`.
(Bulk backfill of a whole existing DB is migration 0001 — see
[[m0001_unified_memory_backfill]].)

## 2026-06-08 — social entities imported via the repo

Social-network import reconstructs `SocialNetworkEntity` objects and writes them through `SocialNetworkRepository.save_entity` (full upsert into `memory_entity`) instead of inserting `instance_social_entities` rows; the row-id rewrite key is `social_entities` and the count comes from the repo. Mirrors the export change in [[builder]].

# importer.py — bundle import pipeline (preflight + confirm)

## 为什么存在

`.nxbundle` 文件 = 跨实例分享 NarraNexus agent / team 的载体。导入端必须同时做完一连串复杂的安全 + 兼容 + ID rewrite 工作，且**必须事务性**（任何一步失败 = 没导入过）。把这些动作集中在一个文件里，让出错路径可控。

## 上下游关系

- **被谁用**：
  - `backend/routes/bundle.py` — 路由层，把上传的 zip 交给 `preflight()`，再用返回 token 交给 `confirm()`
- **依赖谁**：
  - `bundle/security.py` — `extract_zip_safely`、size limits
  - `bundle/id_field_map.py` / `id_schema.py` — ID rewrite 5 层防御 Layer 2 + Layer 1
  - `utils/db_factory.get_db_client` — 写库
  - `bundle_preflight_sessions` 表 — token 持久化（B5 修复）

## 设计决策

### Preflight + Confirm 两步走（PRD §8.5）

UX 要求用户先看预览再决定。preflight 解压 + 解析 + 检测冲突，return token；confirm 用 token 真正写库。

### Token 用 SQLite 表存，不用内存 dict

最初实现用 `_PREFLIGHT_STORE` Python 字典，发版重启 / 多 worker 都会丢。**已替换**为 `bundle_preflight_sessions` 表，6h TTL，inline cleanup。

详见 `.mindflow/project/references/scaling_assumptions.md` §1。

### work_dir 在持久路径下

`~/.nexusagent/bundle_preflight/<token>/`，docker compose 可以用 named volume 挂着。

> ⚠️ **SINGLE-WORKER ASSUMPTION**：work_dir 是本机 fs 路径。多 pod 部署 (k8s with ephemeral storage) 时，confirm 命中另一个 pod 会找不到 work_dir。修复方式：mount RWX volume 或上 S3。

### CPU 重活 → asyncio.to_thread

`extract_zip_safely`、`_extract_tar_safely` 都用 `asyncio.to_thread` 包装，避免阻塞 event loop（影响所有用户的 chat WS）。

### ID Rewrite 设计

5 层防御实现了 Layer 1 + 2 + 4：
- Layer 1 = `id_schema.ID_KINDS` regex 字典
- Layer 2 = `id_field_map.STRUCTURED_ID_FIELDS` 表-列登记
- Layer 4 = 自由文本 regex 兜底（`free_text_regex.sub`）

未做的 Layer 3（CI 反向检查）+ Layer 5（roundtrip test）记在议题 6 后续 TODO。

### Unknown module_class 兜底（2026-05-09）

import 时 `module_class` 不在 `MODULE_MAP` 里的 `module_instances` 行**直接丢弃**（不进 DB），并把 `instance_id` 收集进 `skipped_instance_ids`。同 agent 下的子表 (`instance_jobs`, `instance_social_entities`, `instance_rag_store`, `instance_awareness`, `instance_narrative_links`, memory family) 在 insert 前都做这个集合检查 → cascade-skip。一份 `skipped {n} {Class} instance(s) — module class not registered in this build` warning 加到 `summary.warnings`。

为什么这么做：跨机器 import 经常带"源端有但目标端没装"的自定义 Module（比如 MatrixModule）。如果让这些 row 留在 DB 里，runtime 每个 turn 都会 log `Unknown module type, skipping`，而且永远不会被 cascade-delete（除非 agent 整体被删）。

### Artifacts 入包（2026-05-15，bundle_format 1.1）

pre-collect 阶段扫每个 `agents/<aid>/artifacts.json` 把 `artifact_id` 加进 `id_map`（kind=`artifact`，前缀 `art_`）。写库阶段紧接在 `workspace.tar.gz` 解压之后：
- `rewrite_row("instance_artifacts", ...)` 处理 `artifact_id` / `agent_id`
- `file_path`：bundle 里是 workspace-relative，**重新拼上 `{new_aid}_{recipient_user_id}/`** 还原 DB 约定
- `session_id` / `original_session_id` 一律 `None`，`pinned = 1` —— session 跨实例无意义，强制 pin 保证接收方能在 Settings 页面看到
- `created_at` / `updated_at` 走 DB default

### MCP write-through（2026-05-15，bundle_format 1.1）

1.0 时代 `mcp_hints.json` 只是 hint，import 后用户手动重新建 `mcp_urls` 行。1.1 起：
- pre-collect 时扫 `mcp_hints.json` 给 `mcp_id` 分配新 id
- 末尾 `mcp_hints` 段不再只统计数量，而是 `rewrite_row("mcp_urls", row)` 后真插库
- `connection_status` / `last_check_time` / `last_error` 都重置 → 让本机 MCP poller 重新验证
- **gating**：`bundle_format_version < 1.1` 时跳过 write-through 保留 hint-only 老行为（1.0 包是全自动 include 的，import 端硬塞 mcp_urls 会让接收方意外多出一堆来路不明的 MCP）

### instance_jobs 时间戳保留（2026-05-09）

`instance_jobs.created_at` / `updated_at` 在 schema 里**没 DB 默认值**（不像 `module_instances` 有 `default="(datetime('now'))"`）。importer 历史代码沿用其他表的 "pop timestamp → DB 自动填 now()" 套路，结果就 NULL 进库。`JobModel.created_at` 是非 Optional 的 `datetime`，job_trigger 第一次 poll 就在 Pydantic validation 上炸。

修法：从 bundle 原样拷贝 `created_at` / `updated_at`；只有在 bundle 自己缺失时才回填 `now`。同时跑 `tests/bundle/test_roundtrip.py::test_jobs_preserve_timestamps_on_import` 兜底防回归。

## Gotcha

- 重启后 confirm 报 "preflight working dir missing" = 用户中间发版了，让用户重传。
- ID rewrite 在自由文本里**有概率误命中**普通 hex 串（极低，可接受）。
- 整个 confirm 是非事务的（一个 insert 一个 insert），失败时 staging dir 清掉但已经入库的 row 不会回滚。这是 v1 简化，spec 阶段需要包 transaction。
