---
code_file: src/narranexus/platform/bundle/builder.py
last_verified: 2026-10-28
stub: false
---

## 2026-10-28（PR #419 review I-2）— `BundleForbidden`

新增 `class BundleForbidden(ValueError)`：`assert_team_owned` 与 agent 归属校验（"not owned"）
都抛它，路由按类型映射 403，不再依赖消息前缀这条隐形契约。继承 `ValueError` 让既有
`except ValueError` 调用方行为不变。**未知 agent 的 `ValueError("Agent not found…")` 刻意
不归入**——它落 500，不确认存在性。以后在 builder 里加新的归属检查，抛 `BundleForbidden`
即可得到 403。回归测试：`tests/bundle/test_roundtrip.py` 的 team / agent 两条
（`pytest.raises(BundleForbidden)`，且断言未知 agent 不是该类型）。

## 2026-10-28 — 导出的 team 必须归导出者所有

agent 列表早有 created_by 校验，team 只按 id 读：传别人的 team_id 能把对方团队的名称、
intro、公告栏、工作板打进 manifest。新增 `assert_team_owned`（与 agent 校验同形的
ValueError "Forbidden"），在读 team 行之后立刻调用；`backend/routes/bundle.py` 把它映射成 403。
team 行不存在时放行（没有可泄露的内容）。`build_bundle` 级回归测试在
`tests/bundle/test_roundtrip.py::test_export_refuses_a_team_the_exporter_does_not_own`。
## 2026-10-28（PR #421 复审 Minor 8）— 链接 workspace 降级为逐 agent warning

`_pack_workspace`（async 包装）在丢给线程前先 `refuse_symlinked_dir(src)`，只接住这一个
ValueError：记 log、往 `build_bundle` 的 `warnings` 写一条带 `agent_id` 的说明、返回 None
（该 agent 的 `workspace_path` 为 None，summary 已支持）。其他 agent 照常导出——与 full_copy
skill 目录的逐个降级同口径；之前整次多 agent 导出以一条不带 agent_id 的 500 失败。
`_pack_workspace_sync` 里的拒绝保留（直接调用方的纵深防御）；它的其他 OSError（磁盘满、权限）
**不**被吞，免得变成"workspace 为空"。测试：`tests/bundle/test_export_symlinked_workspace.py`。

## 2026-10-28（复审后）— 打包入口拒绝符号链接目录

`_pack_workspace_sync` 与 `_zip_dir` 开头 `refuse_symlinked_dir(src)`：`os.walk` 对顶层路径
本身是链接时会跟随，导出会打包别人的树。`_zip_dir` 的这次拒绝放在 docstring 之后（放在前面
会让 docstring 变成普通字符串表达式）。

full_copy 分支的降级 `except` 由 `(OSError, UnicodeDecodeError, BadZipFile)` 改为
`(OSError, ValueError, BadZipFile)`：`_zip_dir` 对链接 skill 目录抛的是 ValueError，原来不在
集合里，一个被换成链接的 skill 目录就让整次导出 500，违背"一个坏 skill 不能拖垮导出"的契约。
ValueError 同时覆盖 UnicodeDecodeError（其子类）。`SensitiveZipDetected` 继承 `Exception`
不是 ValueError，仍照常向上抛。测试：`test_skill_archive_path_safety.py::
test_full_copy_symlinked_skill_dir_warns_and_the_export_completes`。

## 2026-10-28 — `_zip_dir` 不再跟随符号链接

`_zip_dir`（full_copy skill 的内层 zip 和最终 `.nxbundle`）里 `zf.write(full)` 会跟随链接
把目标文件打进包；现在遇到 `is_symlink()` 直接跳过。`_pack_workspace_sync`（tar）的两条
路径本来就跳过链接，未改。

## 2026-08-18 五审 — 来源点闸口的适用范围写清楚

补记一个**有意的副作用**：`skill_dir` 的闸装在来源点，因此对 `url` / `builtin`
这两个**根本不用它碰文件系统**的分支同样生效。这是故意的——`entry["skill_dir"]`
会进 manifest、导入侧拿它当目录名用，所以"我们不肯写的值也不该发出去"。

可见代价很窄：某个 skill 的 SKILL.md frontmatter `name` 含分隔符、用 url 方式
导出、且没传显式 `skill_dir` 时，它现在会被跳过并留一条 `unusable skill_dir`
warning，而不是照常导出。选这个而不是"只对 zip/full_copy 拦"，是因为后者会让
导出侧和导入侧对同一个字符串的理解分叉。

另：`per_agent_dir.mkdir()` 挪进了 full_copy 的降级 try（`tgt_zip` 的路径计算
留在外面，因为 except 里要 unlink 它）——四审只裹了 `_zip_dir` / `file_sha256`，
mkdir 真在 ENOSPC 上失败仍会 500，降级契约比注释声称的窄一行。

## 2026-08-18 四审 — `skill_dir` 收到来源点；full_copy 补降级

**上一轮我只修了 zip 分支，这是错的。** `skill_dir` 的闸装在
`_safe_bundle_zip_name` 里，而 `full_copy` 分支根本不经过它——那一侧还是**读**
侧：`_find_skill_dir` 用这个字符串决定读哪个目录，`skill_dir="../../.."` 在
nested 布局下解析到 `base_working_path` 本身，`_zip_dir` 把**所有用户的 agent
workspace** 打进请求者下载的包；配 `include_skill_secrets: true` 连
`.skill_meta.json` 里的凭据一起。**比 SEC-07 原本那个单文件读更严重。**

现在闸在**唯一来源点**（`skill_dir = sanitize_filename(cfg.get("skill_dir") or
skill_name)`），一处覆盖 builtin 前置守卫、zip、full_copy、`_find_skill_dir`，
以及写进 manifest 的 `entry["skill_dir"]`——导出侧存的就是净化值，和导入侧
（`install_skill(target_dir_name=...)` 那边还会再 `sanitize_filename` 一次）对
同一个字符串的理解一致。

> **教训写在这里**：逐分支补闸 = 下一个分支必然漏。这一条我犯了两次
> （SEC-07 漏 `skill_dir`，本轮又只修 zip 半边），所以判据是"这个不受信字符串
> 是在哪一行被**造出来**的"，而不是"它在哪些地方被用到"。

`_safe_bundle_zip_name` 相应退化成只管唯一文件名（`ensure_within_directory` 留
作纵深），并且**自己往 `taken` 里登记**——原先要求调用方记得 `.add()`，等于把
"名字唯一"这个不变式交出去一半。

**full_copy 分支补上降级 try**：`_zip_dir` 走整棵目录树，一个不可读文件 / 磁盘
满 / 非 UTF-8 的 `.skill_meta.json`（按文本读）都会冒成 500。捕
`(OSError, UnicodeDecodeError, BadZipFile)` → 清半成品 + warning + `continue`，
`continue` 落在 `skills_summary.append` 之前，保持"跳过的 skill 整条不出现"这
个约定。**不用 `except Exception`**：`SensitiveZipDetected` 是控制流，必须继续
冒。

> 边界说明（别以为端到端解决了）：文件名唯一性**只到 bundle 为止**。导入侧按
> `skill_dir` pin 安装目录，两个共用同一 `skill_dir` 的 skill 在接收端仍会落进
> 同一个 `skills/{dir}/`、后者覆盖前者。

## 2026-08-18 三审 — `skill_dir` 是 SEC-07 漏掉的那个字段

`tgt_zip = skills_dir / f"{skill_dir}.zip"` —— `skill_dir` 来自导出请求体，
`SkillExportSpec` 上没有任何 validator，一路原样进文件系统路径。SEC-07 堵了
`archive_path`（任意读）和 `skill_name`（写入穿越），**同一个请求体里的
`skill_dir` 漏了**；而本轮新加的 `unlink` 把这条路径从"只写"扩成了"还能删"。

收进 `_safe_bundle_zip_name()`，它同时干两件事：

1. `sanitize_filename` + `ensure_within_directory` —— 客户端字符串不得决定服
   务端路径，和 SEC-07 对 `skill_name` 的处理同一套手法。抛 `ValueError`，调
   用处降级成 per-skill warning，不 500。
2. **真正唯一的文件名**。原来的 `{dir}.zip` → `{dir}__{agent_id}.zip` 只区分
   "不同 agent"：同一个 agent 上两个不同 `skill_name` 声明同一个 `skill_dir`
   时，两者算出的名字一样——成功则后者覆盖前者的字节（前者 manifest 里的
   sha256 就指向了别人的内容），失败则新加的 `unlink` 把前者**删掉**、manifest
   指向一个不存在的 `archive_ref`。改成计数器后缀，`exists()` 分支退化成断言。

另外降级文案的条件收紧成 `isinstance(e, BadZipFile) and src_type != "zip"`：
`copy2` 因磁盘/权限抛 `OSError` 时归档本身没毛病，再说"这是 github 源不是
zip"就是把上一轮刚修掉的误导换个方向重来一遍。

> `archive_ref` 与导入侧是一对（importer 按 `archive_ref` 原样取文件、不解析
> 文件名），所以改命名规则是安全的。

## 2026-08-18 — 坏归档降级：try 覆盖到 copy/sha，文案不再冤枉 tarball

（承接下方 2026-08-18 那条）三处收紧：

1. **try 范围**从"只裹 `scan_zip_for_sensitive`"扩到 **scan + `copy2` +
   `file_sha256`**。只裹 scan 的话，同一个源文件在 copy 阶段抛 `OSError` 仍会
   冒成 500——"一份坏归档不该让整份导出失败"这个不变式就只覆盖了两个失败点里
   的一个。
2. **失败时清理半成品**：`copy2` 中途失败会在 `skills_dir` 留下不完整的
   `{skill_dir}.zip`，而下一个同名条目的 `if tgt_zip.exists()` 分支会因此拐到
   `__{agent_id}` 后缀——换来一个更难查的问题。降级路径里 `unlink(missing_ok)`。
3. **`archive_rows_by_skill` 现在同时带 `source_type`**：github 装的 skill 归
   档是真 `.tar.gz`，导出请求若对它声明 `install_method="zip"`，喂给
   `scan_zip_for_sensitive` 的是一份**完全健康**的 tarball。原文案"你的归档不
   是可读的 zip"会让用户去重传一个好包然后发现症状不变——真正的错配是 method
   与 source_type。现在这种情况给专门的文案，指向 `install_method='url'`。

## 2026-08-18 — 坏归档降级成 warning，不再整份导出失败

`scan_zip_for_sensitive(Path(src_zip))` 原先裸调用，`BadZipFile` 一路冒到路
由变成 500。上传侧现在会挡住新的坏包（见 [[bundle.py]]），但**已经在库里的
坏行**挡不住：早于上传校验写入的、写了一半被截断的、磁盘上损坏的。

现在捕 `(BadZipFile, OSError)` → warning 带 skill 名 + `logger.warning` 带
user/skill/path → `continue`。形状照抄它上面那条 `zip not found, skipping`，
这样 manifest 里的约定只有一种：**跳过的 skill 直接不出现在 skills 列表
里**（不是出现但没有 `archive_ref`）。

关键不是"别 500"，是**一份坏归档不该让整份导出失败**——一个用户的一条脏数
据会把他所有导出都堵死，而错误信息指不到是哪一份。

## 2026-08-17 — SEC-07：zip 归档路径由服务端解析，且读前复查 containment

`install_method="zip"` 分支原先是 `cfg.get("archive_path") or
cfg.get("manual_zip_path")` —— 这两个值一路来自 **HTTP 请求体**
（见 [[bundle.py]] 的 `SkillExportSpec`），然后被 `shutil.copy2` 拷进
导出包并流回调用者。任何登录用户传 `archive_path: "/etc/passwd"` 就能
读走后端进程能读的任意文件。

改成两道：

1. **服务端解析**：进 skills 循环前一次性把该 `user_id` 的
   `skill_archives` 行读成 `archive_paths_by_skill`，zip 分支只查这张表。
   请求里已经没有路径字段可传了。立场和上面那道"内置技能强制走 builtin"
   的 server-side guard 一致：**客户端选 method，服务端定 bytes**。
2. **读侧 containment**：DB 里可能还留着修复前写坏的行（dev 库 id=20 就
   是），所以打开前过一遍 [[skill_backup.py]] 的
   `is_within_user_archive_dir`；越界就 skip + warning + `logger.warning`
   带 user/skill/path，而不是信任这一列。锚点必须是**该用户的目录**而不
   是 archives root：id=20 存的字符串 resolve 之后落在 root 里面
   （`{root}/marker.zip`），root 锚点会放行；`{root}/{受害者}/x.zip` 也一
   样。三条中毒用例分别覆盖 root 外、root 散装层、跨用户目录，后两条在
   root 锚点下会红。

`cache_key` 从 `f"{skill_name}|{src_zip}"` 改成 `skill_name`：`src_zip`
现在是 `skill_name` 的纯函数（`skill_archives` 在 `(user_id, skill_name)`
上唯一），复合 key 恒等于单 key。原注释说的"同名不同目录的 skill 各拿
自己的 archive_ref"在服务端解析之后**结构上已不可能**——同名必然解析到
同一行、共享同一份 bytes。注意 `skill_dir` 那层文件名去重是**另一件事**
（包内文件名撞车），别一起删。
## 2026-08-10 — 工作板随 team 一起导出

`_export_work_items`。板子**就是**这个团队欠着的东西,只还原房间不还原欠账,
迁移过去的团队看起来就像已经全部做完了。

只导未完成项:`done`/`cancelled` 属于历史(跟聊天记录一起走),而 **`paused`
要导** —— 那是接收方继承的一个真实待决事项。

**`root_run_id` 刻意不导出**:它指的是**源环境**里的一个 run。带过去会让目标
环境的一次级联停止匹配到它从未产生的工作项 —— 一次悄悄 park 掉别人工作的停止。
接收方的板子就以「无树关联」开始,这是诚实的状态。

`assignee_id` 保留为源 id,由 importer 与其它 agent id 一起重映射。

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

E2E 覆盖（operator-local live-stack 工具箱承担，不入库）：seed 一个 `narrative_info.description` 含 SECRET 短语的 narrative → POST `/api/bundle/export` 两次（chat history on / off）→ unzip 后断言 off 那次 SECRET 不存在。

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

## 2026-08-11 — manifest 携带团队公告栏

`team_meta` 增加 `bulletin`。丢弃逻辑（不带自动总结、不带 author_id）在
[[team_bulletin_transfer]]，与导入侧的「不可信」规则放在一起——出去时丢掉的，
正是进来时不能信的。

## 2026-09-04 · credentials exported from the generic store (batch 4d.2)

`channel_credentials.json` is now `{"channel_credentials": [...]}` built from `GenericCredentialStore.list_for_agent(aid)` (`to_raw_dict`: public fields + DECRYPTED secrets — the encryption key is per install, so a bundle must carry plain values). `STRIPPED_TABLES` lists `channel_credentials` so the generic table never leaks through the table-driven paths; the opt-in file is the only way a binding leaves.

## 2026-09-07 — channel credentials are scrubbed and unreadable rows skipped

The generic-store export path had dropped _scrub_user_id: the exporter's NarraNexus user id (and password_hash/secret/api_key keys) left the machine inside channel_credentials.json. Every row now goes through _scrub_user_id(row, user_id, 'channel_credentials') — IM-side ids survive because the table's ID columns are in STRUCTURED_ID_FIELDS. A record whose secret this install cannot decrypt (secret_error set) is skipped with a warning rather than exported as an empty secret.
