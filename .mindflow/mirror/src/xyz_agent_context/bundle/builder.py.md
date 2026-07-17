---
code_file: src/xyz_agent_context/bundle/builder.py
last_verified: 2026-07-15
stub: false
---

## 2026-07-15 — mcp_hints 安全不变式：headers 绝不出境

`mcp_urls` 新增 `headers` 列（Authorization token 等密钥）。mcp_hints.json 的
字段选择保持显式白名单，**headers 永不加入**——bundle 会离开作者账号，收件人
自己配置凭据。代码内已加 SECURITY INVARIANT 注释锁死。

## 2026-07-13 — opt-in skill secrets (scrub by default)

`ExportSelection.include_skill_secrets` (default False, the 'full mode' companion of include_channel_credentials). When OFF: the workspace packer blanks each `.skill_meta.json`'s `env_config` VALUES (via `bundle/skill_secrets.py`) and `_zip_dir` sensitive-filters the full_copy archive (drops credentials.json etc. + scrubs meta) — so no skill secret leaves silently. When ON: both ride along + `contains_secrets` is set. Manifest gains `contains_skill_secrets`; `stripped` lists `skill_secrets` when not opted in.

## 2026-07-10 — opt-in IM channel credential export

`ExportSelection.include_channel_credentials` (default False) makes the
per-agent loop also emit `agents/<aid>/channel_credentials.json` — the closure's
rows from the six credential tables (lark + channel_{slack,telegram,wechat,
discord,narramessenger}), grouped by table. `manifest.contains_channel_credentials`
flags it so the import wizard warns. The table list is the shared single source
of truth `bundle/channel_credential_tables.py::CHANNEL_CREDENTIAL_TABLES`.

**Important**: `STRIPPED_TABLES` / `AGENT_SCOPED_TABLES` / `INSTANCE_SCOPED_TABLES`
are DEAD constants (documentation only — zero references; the real export is the
explicit `db.get` calls). So credential "stripping" was always by-omission, not by
that set — the opt-in export just adds the reads. The mirror's older "凭证一律剥离"
decision is therefore now: **stripped by default, shipped on opt-in.** `agent_id`
is kept verbatim in the exported rows (import remaps it via STRUCTURED_ID_FIELDS);
everything else is IM-side and preserved. On import each row lands INACTIVE.
Also removed a dead contradictory `lark_trigger_audit` entry that sat in BOTH
`AGENT_SCOPED_TABLES` and `STRIPPED_TABLES`.

The manifest `stripped` list (shown verbatim in the import preview's "not present
in bundle" section) is now DYNAMIC: `im_channel_credentials` is listed only when
the user did NOT opt in — otherwise the preview would contradict the shipped
`channel_credentials.json`. `api_keys` / `user_password_hash` / `user_providers`
stay always-stripped (the old hardcoded `lark_oauth` label was renamed to
`im_channel_credentials`, since it now covers all six channels). Tests:
`tests/bundle/test_channel_credentials.py`.

## 2026-06-09 — manifest stamps the live app version

`narranexus_version_exported` was a stale hardcoded `"1.3.4"`; it now reads
`_current_app_version()` → the package `__version__` (= pyproject
`[project].version`, e.g. 1.7.12), so every export records exactly which build
produced it. `bundle_format_version` is unchanged (still "1.1" — the on-disk
format is untouched).

## 2026-06-08 — social entities exported via the repo

Social-network export no longer reads `instance_social_entities` directly; it goes through `SocialNetworkRepository.get_all_entities` (which now reads `memory_entity`) via an `_entity_to_flat` helper. The scrubbed bundle key is `social_entities`. Keeps export aligned with the entity-into-engine fold.

## 2026-05-19 — "Disable chat history" 还漏了 ChatModule 的主消息存储表

接 2026-05-18 那一轮 fix。Bin哥 复测发现：勾掉 chat history、打包再 import 之后，**imported agent 里的 chat history 仍然完整可读**（直接显示在前端对话视图里）。

根因：上一轮把 `events.jsonl` / `agent_messages.jsonl` / `narrative.json` 全部 gate 住了，但**漏掉了 builder.py 行 469-481 那个 memory family 循环**。这个循环无条件导出 4 张存对话的表：

- `instance_json_format_memory_chat` —— ChatModule 的主消息存储（`memory` 列是 `{"messages": [{role, content, timestamp}, ...]}` 原文 JSON）；`get_chat_history` MCP tool 直接查这张表（见 `module/chat_module/_chat_mcp_tools.py:73`）。**这就是用户在前端能看到的 chat history 的源头。**
- `instance_json_format_memory` —— Slack / Telegram / EventMemory 等 IM 模块的对话缓存
- `instance_module_report_memory` —— LLM 蒸馏的对话摘要（per-instance）
- `module_report_memory` —— 同上但按 narrative 维度（EventMemoryModule 写）

Import 侧（`importer.py:844-864`）也无条件读这 4 张表 → instance_id rewrite 后插入新 agent → 新 agent 的 `get_chat_history` 立刻返回原 owner 的对话。所以 toggle 名义上 disabled，bundle 里有数据，import 后又落回 DB，前端就看到了 chat history —— **隐私完全没堵住**。

**修法**：把 4 张表的导出循环都 gate 在 `selection.include_chat_history` 上。False 时写 `[]`（不是跳过写文件 —— 保留空文件让 importer / bundle inspection 工具看到"该 section 存在但被刻意清空"，语义更明确）。

测试：`tests/bundle/test_roundtrip.py::test_chat_history_disabled_strips_memory_chat`（seed 一个含 `SUPERSECRET_PASSPHRASE_42` 短语的 `instance_json_format_memory_chat` 行 → 导 bundle with disable → 断言 bundle 里 memory_chat JSON 为空 + 整个 zip 任何 member 都不含 SECRET 短语 + 重新 import 后新 agent 的 memory_chat 为空）+ `test_chat_history_enabled_keeps_memory_chat`（happy-path 反向断言，enable 时 round-trip 保留 memory）。两者都在 12 个 bundle 测试中 0 regression 通过。

## 2026-05-18 — "Disable chat history" 实际上要 disable narrative_info 里的对话副本

用户反馈："team 打包 bundle 的时候，disable chat history 这个 function 不 work"。复盘后发现 gate 只覆盖了 `events.jsonl`（行 257）和 `agent_messages.jsonl`（行 330），**没覆盖 `narrative.json`**。但 `narratives.narrative_info` 这个 JSON 列里塞了一堆 chat 派生数据：

- `narrative_info.description` —— framing prompt 里 copy 过来的最近 N 条原文对话（Matrix conversation history 那种）
- `narrative_info.current_summary` —— LLM 写的对话摘要
- `narratives.dynamic_summary` —— 按 event 顺序的逐条 mini-summary
- `narratives.topic_keywords` / `topic_hint` —— 从对话蒸出来的话题词
- `narratives.routing_embedding` —— 对话内容的向量（不可读但可被攻击者反推话题）

所以用户关掉 toggle、bundle 里没有 `events.jsonl` 也没有 `agent_messages.jsonl`，**但 `narratives/<id>/narrative.json` 里仍能读到几轮原文对话**。功能名义上 disable 了，实际隐私没堵住。

**修法**（与 Bin哥 对齐的产品决策）：用户选了某个 narrative → 留 narrative 骨架（id/type/actors/name/instances/timestamps/related ids/is_special/round_counter/env_variables），剥所有 chat 派生字段。Jobs 仍然跟随 narrative_selection 走（不变）。helper `_scrub_narrative_chat_content(row)` 一处实现：

- `narrative_info` JSON：仅保留 `name` + `actors`，把 `description` 和 `current_summary` 清空
- `dynamic_summary` → `"[]"`
- `topic_keywords` → `"[]"`
- `topic_hint` → `""`
- `routing_embedding` → `None`
- `event_ids` → `"[]"`（events 没导出，留 dangling 引用反而误导）

调用点：narrative 导出循环（builder.py 行 247-256），按 `include_chat_history` 二选一 —— True 走原 row，False 走 scrub helper。其他 chat-gate 行为（events.jsonl / agent_messages.jsonl 缺席）保持不变。

**未处理的潜在 leak**（同源问题，Bin哥 没明确指示就先不动）：
- `module_instances.config` 里 ChatModule 实例可能存近期 chat 状态
- `instance_awareness` 里 agent 对用户的认知（基于对话形成）
- `instance_social_entities.persona / description` 里"用户画像"也来自对话

这些字段同样属于"chat 派生但藏在别处的副本"。未来如果用户反馈"还有泄漏"再扩 helper。

E2E 测试：`real_case_e2e_test/cases/bundle_chat_history_scrub/run_test.py` 直接 seed 一个 `narrative_info.description` 含 SECRET 短语的 narrative，POST `/api/bundle/export` 两次（chat history on / off），unzip 后断言 off 那次 SECRET 不存在。

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

## 2026-07-10 — workspace tar 排除内置技能

- `_pack_workspace_sync` 通过 `_builtin_skill_relpaths(src)` 求出 `skills/<name>` 为 `builtin:true` 的目录集，在 fast-path 的 `filter_func` 和 user_id 改写的 manual-walk 两条路径都跳过它们——否则内置技能字节会混进 `workspace.tar.gz`（`skill_methods` 排除不了这条，因为 workspace tar 独立打包整个目录）。

## 2026-07-14 — `skill_methods` 导出路径的服务端内置守卫

- 上面 workspace-tar 那条只堵住了「整目录打包」；显式 `skill_methods`（由客户端 `install_method` 驱动的 zip / full_copy）是**另一条**导出路径，之前**没有**内置守卫。绕过前端或前端有 bug 时，内置技能会被打成 `archive_ref` 当用户数据外发。
- 现在这条 loop 里 `zip` / `full_copy` 分支前先 `_find_skill_dir` 定位磁盘目录，`dir_is_builtin`（[[skill_secrets.py]] 单一真相源）命中即把 `method` 强制降级为 `builtin`（空载、无 `archive_ref`），并记一条 warning。至此「内置永不作为用户数据旅行」在**两条**导出路径都成立。
- `_builtin_skill_relpaths` 也顺手改用 `dir_is_builtin`，不再自己读 `.skill_meta.json`，语义与其它三处一致。
- 回归测试:`tests/bundle/test_skill_import.py::test_builtin_skill_forced_to_builtin_method_despite_full_copy_request`（客户端请求 full_copy 内置技能 → manifest 记 `builtin`、bundle 内无该技能归档）。
