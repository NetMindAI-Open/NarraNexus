---
code_file: src/narranexus/platform/bundle/importer.py
last_verified: 2026-10-28
stub: false
---

## 2026-10-28（二审）— `artifacts.json` 行改写提成 `rewrite_artifact_row`

导入循环里"校验 `file_path` → 前缀接收方 workspace → 清 session、置 pinned、去时间戳"整段
提成模块级 `rewrite_artifact_row(new_ar, new_aid, user_id)`，调用点只剩一行，因此校验确实
被调用这件事可以直接测（`..` → ValueError；正常路径 → 带 `{user}/{agent}/` 前缀）。
ValueError 不在该段的 `except (OSError, JSONDecodeError)` 里——坏指针仍是终止整个导入，
不是跳过一行。

## 2026-10-28（复审后）— `copy_skill_into_agent` 提到模块级；tar 只解普通文件/目录

闭包 `_copy_skill_to_agent` 的实体提成模块级 `copy_skill_into_agent(src, aid, user, name, base=)`，
符号链接行为可直接测试；闭包只做转发。`_extract_tar_safely` 的手工过滤改为只放行
`isfile()/isdir()`（原来只剔链接，FIFO/设备会被解出来），`filter="data"` 作第二道（剥 setuid）。

## 2026-10-28 — 包内容是攻击者可控的：三处收口

- `artifacts.json` 的 `file_path` 新增 `artifact_relpath_or_raise`：复用
  `validate_zip_member_path`，`..`/绝对/反斜杠/NUL/空一律 ValueError 终止导入（原来只
  `lstrip("/")`）。
- `_copy_skill_to_agent` 改走模块级 `copy_skill_dir`（`copytree(symlinks=True)`）：不再把
  克隆仓库里的符号链接解引用成 workspace 里的真实文件。
- `_extract_tar_safely` 的 `extractall` 加 `filter="data"`，拒绝设备/FIFO 条目。

## 2026-09-09 — url 安装法只装 bundle 行点名的那个 skill（GitHub #95，复审 C4）

`install_cache_key` 以前读 `skill_name`，而 builder 写进 manifest 的键是 `name`，键恒为 None——单 skill 仓时代
无害，多 skill 仓下同一仓的两行撞同一缓存槽，第二个 agent 会拿到 skill A 的文件、顶着 skill B 的名字。现在
key 读 `name`（zip 分支同修）。url 分支不再调 `install_from_github`（那会把整仓装进第一个 agent），改为
`_install_one_skill_from_github`：`fetch_github_repo` 后按 SKILL.md name 或目录名（manifest `skill_dir`，否则
sanitize 后的 name）挑出唯一匹配的根，只 `install_from_dir` 它；找不到 → `skill_install_failures` 记一条
"ships no skill named …(found: …)" 并 continue，**绝不取第一个兜底**。测试：
`tests/bundle/test_skill_import.py::test_url_rows_of_one_multi_skill_repo_each_land_their_own_skill`。

## 2026-08-20 — 改名的导入现在会纠正身份记忆

本文件**会改名**:`unique_value` 加去重后缀、clamp 截长、空名兜到
`"Imported Agent"`,`renamed = (final_name != clamped_name)` 就是这个判据。而
`instance_awareness` 是**逐行复制**的,于是导入进来的 agent 行里是
`小绿 (1)`、自己的 profile 却继续声明 `小绿` —— 深圳第二轮那个状态,从导入路径进来。

已在 awareness 行插入**之后**接一次 `reconcile_identity_record(db, new_aid,
final_name)`。三个要点:

- **顺序要紧**:必须在 `_ins("instance_awareness", ...)` 之后,否则更正写进一个还
  不存在的行。
- **用对账,不用改名事务**:那个事务的前置是「行已存在且可写」,这里行刚被创建、
  且里面已经是最终名字。
- **天然幂等**:profile 与行一致时返回 `None`,所以每次导入都调是安全的。

返回 `False`(发现了不一致但没修成)时打 warning —— 那正是事故本体的状态,不该只留在
一条会被轮转掉的日志里以外什么都没有。

⚠ `tests/schema/test_only_one_writer_of_agent_name.py` 的 allowlist 里,本文件那条
理由**曾经写的是「创建路径无需纠正旧名」——对本文件是假的**。已改成陈述真实情况。
闸门的价值等于它理由的可信度:下一个扩那个列表的人会照抄上一条的说辞。

## 2026-08-19 — PR#327 I1:唯一冲突判定收敛到共享谓词(六处里唯一被收紧的一处)

`instance_narrative_links` case 2 那个 `except` 分支的判定从本文件的裸字符串匹配换成
共享 [[dialect_errors.py]] 的 `is_unique_violation`。六处收敛里,**只有这一处是被收紧的**:
原判定含裸 `unique` / `duplicate` 子串,新谓词要求**完整短语**。收紧安全,因为两方言的真实
冲突文本都含完整短语——sqlite 抛 `UNIQUE constraint failed: ...`、mysql 抛
`Duplicate entry ... 1062`(SQLite Proxy 只是**前缀**包装、不是替换,`... error (/insert):
UNIQUE constraint failed` 仍命中)。

判**错**方向也随之翻转:旧的裸子串会把文本恰好含 `unique`/`duplicate` 的**无关**失败
误判成冲突 → 悄悄吞掉当成"已去重";新谓词更严,漏判方向变成把**真冲突**误当无关错误
`raise` 出去 → `confirm()` 中途 abort、触发回滚、部分导入失败。所以这一处必须有真库真冲突
的测试兜住(见 `tests/bundle/test_narrative_link_dedup.py`:两个 agent 共享同一 instance
的 bundle,断言 case 2 走 dedup 而非抛)。

## 2026-08-18(补)— 空名兜底,五条创建路径就此闭环

`final_name = final_name or "Imported Agent"`,放在 dedupe **之后**。

本文件是五条创建路径里最后一条还能把**空名**存进库的:auth 路由回退
`"New Agent"`、[[applier.py]] 回退 `"Imported Agent"`、两条 `create_agent` 腿直接拒,
而这里归一出 `""` 之后没有兜底,`dedupe_name` 无冲突时原样返回就落库了。
bundle 是用户上传的 zip,`agent.json` 里名字为空或纯空格是很正常的输入;
存成空名的 agent 在侧栏、peer 名录里都只能显示裸 `agent_id`。

**放在 dedupe 之后**:放之前,每一个空名 bundle 都会去和库里已有的
`"Imported Agent"` 撞名、被加 `" (n)"` 后缀;放之后同名重复是允许的
(`agent_name` 无 UNIQUE,上面的注释已说明)。

测试:`tests/bundle/test_agent_field_normalization.py`
`test_an_empty_bundle_name_gets_the_import_fallback`,用「删掉这行再跑」验证过会红。

## 2026-08-18 五审 — manifest 的 `agents` 一并收口

四审给 `archive_ref` 加了闸，但 `manifest["agents"]` 的每一项同样会变成
`work_dir/agents/{aid}/agent.json` / `.../channel_credentials.json` 的路径段，
仍然裸着——**同一份 manifest、同一类字符串，只收了一个字段**，正是四审刚写下
的"逐字段补闸"教训的又一次现场。

现在闸在 **manifest 入口**（preflight 读完 manifest、任何人用它拼路径之前），
每一项过 `sanitize_filename`，不合格 **整份 bundle 拒掉**。

拒整份而不是跳过该 agent 是有意的：`aid` 同时是 `id_map` 的键、也出现在 summary
里，过滤一半会让 id_map 与 per-agent 写入对不上（`id_map[old_aid]` 直接
KeyError）。legacy bundle 的 agent id 形状不保证是 `agent_*`，所以判据用
`sanitize_filename`（单段 / 无分隔符 / 非 `..`），不是前缀断言。

最现实的命中面不是宿主机随机路径，而是**另一个用户同时在做导入**时的
`bundle_preflight/nx-import-*`（前缀固定，只有 mkdtemp 后缀要猜）。

## 2026-08-18 四审 — `archive_ref` 也是不受信字符串

`zip_path = work_dir / archive_ref` 两处（zip / full_copy 分支）。`archive_ref`
来自**被导入 bundle 的 manifest**，即由 bundle 作者完全控制，却被原样 join。
`"../../../root/.nexusagent/.../x.zip"` 让导入流程读 work_dir 之外的文件，zip
分支随后把它 `copy2` 进导入者自己的 `skill_archives` 并装成 skill——之后这个用
户导出时，这份"战利品"会正常随包走出去。

这是导出侧 `skill_dir` 的**镜像面**：同一个 bug 类，只是不受信字符串来自
manifest 而不是请求体。收进 `_bundle_member_path(work_dir, archive_ref)`：
`validate_zip_member_path` 校验段序列 → resolve 后判前缀 → 不安全返回 `None`。
返回 `None` 而不是抛：调用方本来就把 `None` 当"包里没有这个归档"处理、记进
`skill_install_failures`，形状不变。

⚠️ 用 `validate_zip_member_path` 而**不是** `ensure_within_directory`：合法
`archive_ref` 是多段路径（`skills/{agent_id}/{name}-full.zip`），后者只接单段，
直接套会把所有正常 bundle 打死。

## 2026-08-17 — SEC-07：manifest 里的 `skill_name` 也是不可信输入

skills 段原先自己拼 `skill_archives_dir / f"{skill_name}.zip"` 和
`_full.zip` 两处。这个 `skill_name` 来自**导入的 bundle manifest**，也就是
谁做的 `.nxbundle` 谁写的——和一个表单字段同级的不可信度。同一个文件里
1328 行给 skills 目录做了 `sanitize_filename`，唯独归档这两处漏了。

两处改走 [[skill_backup.py]] 的 `prepare_archive_target()`，`skill_archives_dir`
局部变量随之删除。恶意 manifest 名现在抛 `ValueError`，被 skills 循环既
有的 `except Exception` 记成**单个 skill 的 install failure**（进
`skill_install_failures` / warnings），不会中断整份 import——和其他
per-skill 失败一致。

### 同批修掉的既有缺陷：zip 分支的归档行从来没写进去过

zip 分支把已经就位的 `tgt` 交给 `backup_after_api_install`，而那个 helper
是为"把外面的 zip 拷进登记处"设计的——它自己又算一次
`archive_target`，得到同一个路径，`shutil.copy2(tgt, tgt)` 必抛
`SameFileError`，被它那个宽 `except Exception` 吞成一条 warning，于是
`register_archive` 永远不执行。**导入含 zip 方式 skill 的 bundle 后，该
用户的 `skill_archives` 里没有对应行。**

后果不是"导出少一个 skill"（前端在没有归档行时默认走 full_copy，改动前后
都一样），而是**导入来的 skill 永远无法以 zip 方式再导出，只能 full_copy
——包更大，且 full 模式会把 secrets 一起带走**。

现在 zip 分支直接 `register_archive(archive_path=str(tgt))`，形状照抄
full_copy 分支。回归测试 `test_imported_zip_skill_registers_archive` 钉住这
条，改回旧写法会红。

**sha256 一律由落盘文件现算，不取 manifest 的值**（2026-08-17 二审修正）。
初版写的是"优先用 manifest 的，因为包内 zip 与落盘文件逐字节相同"——这句
有两个反例：

1. **de-dup 哨兵**：[[builder.py]] 对同一个 skill 的第 2..N 条 entry 写的是
   `sha256: "shared"`（它们共用一个 `archive_ref`）。importer 对每条 entry
   都 upsert 一次、后写覆盖先写，于是"两个 agent 共用一个 zip skill"的
   bundle 导入后，`skill_archives.sha256` 就是字面量 `"shared"`。`or` 只兜
   `None` / 空串，兜不住哨兵。
2. **`tgt` 已存在时不拷贝**：上面那句 `if not tgt.exists()` 意味着用户此前
   自己传过同名归档时保留旧字节，而 manifest 的 digest 描述的是新 zip。

这一列的唯一用途就是完整性，写进一个已知有时为假的值比多算一次 hash 差得
多。`test_shared_skill_import_records_a_real_sha` 用**两个 agent**的 bundle
钉住它（单 agent 复现不出来），断言 `re.fullmatch(r"[0-9a-f]{64}", ...)`，
并先断言 manifest 里确实带了 `"shared"` 哨兵——否则用例会因为错误的理由变
绿。

> `full_copy` 分支的 `s.get("sha256", "imported")` 是同一类哨兵，但不在本次
> 改动面上，要改单独一条 commit + 自己的用例。

> 这个缺陷藏了这么久的直接原因是 `skill_backup.py` 那个宽
> `except Exception`（铁律"不要为了日志干净吞异常"）。收窄它是紧跟其后的
> 独立 commit，不和本条混在一起，否则说不清哪个修复对应哪条测试。

## 2026-08-17(补)— dedupe 之后要**重新归一**,不只是重新 clamp

原来的注释论证了「clamp 必须在 dedupe 之后再来一次,因为 ` (n)` 自己没有长度预算」
—— 同一个论证对**归一**同样成立,当时只做了 clamp。`dedupe_name` 在候选名为空串时
返回 `" (1)"`,**带前导空格**,而这个值不再经过任何归一就进了 `_ins`,于是成了本 PR
之后唯一还能产出未归一 `agents` 行的路径(命中很窄:bundle 里名字为空/纯空格,且
该 owner 名下已有一行空名 —— 现实中只能由上一次同样的导入产生)。

现在是 `_clamp_agent_text(normalize_agent_text(deduped_name))`。顺序契约完整表述:
**归一在 dedupe 前**(否则带空白的名字匹配不上库里已归一的同名行,该去重的没去重),
**dedupe 后归一 + clamp 各再来一次**(` (n)` 既没有长度预算也没有空白预算)。

测试:`tests/bundle/test_agent_field_normalization.py`(5 条)。其中区分「归一在
dedupe 前」与「在后」的**只有一条输入**:库里已有归一的同名行 + bundle 带空白的同名,
断言最终值是 `小绿 (1)`。注意 `agents_renamed == 1` **不是**判别式 —— 错误顺序下
它也为真(post-dedupe 归一让 `final_name != clamped_name`),测试里就地注明了。
两条都用「移动那行代码再跑」验证过确实会红。

## 2026-08-17 — 导入的 agent 名字/描述先归一,再 dedupe、再 clamp

`_ins("agents", …)` 是绕过 [[agent_repository]] 的直写,所以归一必须在这里自己做:
库里存着 `" 小绿 "` 的行**永远改不了名** —— 改名路径([[auth.py]])比较归一后的值,
owner 存去掉空白的同名会被判「已相等」,一次写都不发而接口答成功。
装一个名字带空白的 bundle 就会落下这样一行(team marketplace 安装走同一条路)。

**顺序是三步,不能换**:`normalize_agent_text` → `dedupe_name` → `_clamp_agent_text`。

- 归一在 dedupe **之前**:带空白的名字不会与库里已归一的同名行匹配上,
  该去重的没去重。
- clamp 仍在 dedupe **之后**再来一次(原有理由不变):`dedupe_name` 追加的
  " (n)" 自己没有长度预算,255 + " (1)" = 259 会重新越过上限。

## 2026-08-11 — bundle 导入 MCP URL 加 SSRF 筛（安全审计 P0-3）

`mcp_urls` 写入路径（hint→`_ins`）此前**不做任何 URL 校验**，绕过了 create/update 路由的
`_blocks_internal_url`——一个普通用户上传含内网 MCP URL 的 `.nxbundle` 就能把
`http://169.254.169.254/...` 之类种进库，随后被 agent 运行时 fetch。现在写库前跑同一道
**cloud-only** DNS-free 筛（`is_obviously_non_public_url`，**parse-safe**：畸形/非串 URL 判为不安全而非抛异常——裸 `urlparse` 会因坏 IPv6/非串炸出去、经 `_rollback_partial_import` 把整份 import 回滚）：命中即 skip 该行、warning 带 host、
其余 import 照常。local/桌面不筛（localhost MCP 合法）。

## 2026-08-10 — 工作板还原、id 重映射与回滚

board 随 team 行一起写入,三处 id 各有各的处理:

- `assignee_id` 经 `id_map` 重映射;**落在导出闭包之外的 assignee 变成未认领**
  —— 这是真的、且可行动的状态,好过一个没人能追的悬空引用。
- `root_run_id` 恒为 NULL(导出侧已丢弃,理由见 [[builder]])。
- `channel_id` **在 bus channel 还原之后回填**:team 行写在 channel 之前。
  bundle 没带聊天时保持为空 —— 板子在 UI 上照样完整可用(那个视图按 team_id
  查),只有巡查要等房间建起来,而巡查本来也需要一个房间才能说话。

回滚清单同步加了 `team_work_items`,否则失败的导入会留下一块孤儿板子。

## 2026-07-23 — 导入修剪超长 agent_name / agent_description(#71)

confirm 写 `agents` 行前,用 `_clamp_agent_text` 把 name/description 截到
`AGENT_TEXT_MAX_LENGTH`。**顺序关键:先 dedupe 再截 `final_name`**——`dedupe_name`
撞名时追加 ` (n)` 后缀且没有长度预算,若先截到 255 再 dedupe,`"…255… (1)"` 会
变成 259 又越界(review #1)。所以对 dedupe 结果再 clamp 一次,`final_name` 才是
真正入库的值;`agent_name` 无 UNIQUE 约束,截后偶发同名无害(等同手动建同名)。
根因:raw `db.insert` 绕过 Agent 模型的 max_length,超长值入库后每次编辑/删除
反序列化都炸 string_too_long(insertable-but-unreadable)。被截的 agent 记进
`written_summary["agent_fields_trimmed"]`(`[{agent_name, fields}]`)并各加一条
`warnings`——修剪只发生在 `confirm`(_confirm_inner 写库阶段),`preflight` 不改
数据,所以是 **confirm 返回的 summary** 里能看到哪些 agent 被截(前端 done 屏
现在也列出 warnings 正文,见 [[BundleImportPage.tsx]])。
测试:tests/bundle/test_agent_field_length.py(含重复导入的后缀越界回归)。

## 2026-07-13 — skill install to the known skill_dir

The full_copy + zip skill-install branches pass the manifest's `skill_dir` as `install_skill(..., target_dir_name=)`, so a full_copy overwrites `skills/<skill_dir>/` (restoring the credential the workspace snapshot stripped) instead of landing in a temp-derived name. Fixes the arena double-dir bug.

## 2026-07-10 — opt-in IM channel credential import (force-inactive + clash skip)

Imports `agents/<aid>/channel_credentials.json` (opt-in bundles). Two invariants:

1. **Force-inactive**: every credential lands with its active flag = 0
   (lark `is_active`, others `enabled`), regardless of the source value. This is
   the anti-double-connect guard — the user must manually activate in the new env
   (via the channel settings toggle → `POST /api/<ch>/set-active`), which is what
   claims the single connection slot each IM issues per bot.
2. **Clash skip**: a credential whose bot-identity (lark `profile_name`; slack
   `team_id`+`bot_user_id`; telegram/discord `bot_user_id`) is already bound in
   the target env is SKIPPED, not overwritten (stealing a live bot would be
   destructive). `preflight` surfaces these as `credential_clashes`; `confirm`
   enforces the skip and counts it.

`rewrite_row` now EXEMPTS credential tables from the user-attribution
force-overwrite loop: slack/telegram/wechat `owner_user_id` and discord
`owner_user_id`/`user_id` are IM-side ids, NOT NarraNexus user ids — reattributing
them would corrupt the owner-trust signal. `agent_id` is still remapped via
STRUCTURED_ID_FIELDS. Rollback already sweeps every agent_id-bearing table so the
credential rows are covered automatically. Table metadata is the shared
`bundle/channel_credential_tables.py`. Tests: `tests/bundle/test_channel_credentials.py`.

## 2026-06-11 — legacy-bundle tolerance + rollback (v1.3.4 import bug)

A real v1.3.4 bundle failed to import; two independent root causes,
both environment-dependent (see tests/bundle/test_legacy_bundle_import.py
docstring matrix):

1. **Schema drift**: bundle rows carry columns later removed from the
   schema (narratives.embedding_updated_at, unified-memory refactor) —
   fatal on FRESH DBs only, because auto_migrate never drops columns so
   old DBs still accept them. Fix: `_sanitize_for_schema` strips
   unknown columns on every confirm() insert (`_ins` wrapper, 19 call
   sites; preflight bookkeeping insert excluded) and counts them into
   `written_summary["dropped_legacy_columns"]`.
2. **Stringly-typed model reconstruction**: the social-entities branch
   is the ONE importer path that rebuilds a pydantic model instead of
   inserting a raw row (its destination moved to the unified memory
   store); bundle list/dict fields are JSON strings → ValidationError.
   Fix: `_loads_maybe` decodes before construction.

**Atomicity**: confirm() is now a thin wrapper around `_confirm_inner`;
on ANY failure `_rollback_partial_import` sweeps every registered table
carrying agent_id (plus teams/bus by id) for the ids minted in id_map —
no more orphan teams from failed imports. Rollback is best-effort
per-table and never masks the original error. Skill pack FILES are
deliberately not rolled back (shared across agents; re-import
overwrites).

Composite narrative ids (agent_<hex>_<user>_default_N-01): the global
id regex matches the embedded agent id exactly and the same id_map is
applied everywhere, so composites stay internally consistent — pinned
by test.

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
  - `utils/db/db_factory.get_db_client` — 写库
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

import 时 `module_class` 不在 `module_registry` 里的 `module_instances` 行**直接丢弃**（不进 DB），并把 `instance_id` 收集进 `skipped_instance_ids`。同 agent 下的子表 (`instance_jobs`, `instance_social_entities`, `instance_rag_store`, `instance_awareness`, `instance_narrative_links`, memory family) 在 insert 前都做这个集合检查 → cascade-skip。一份 `skipped {n} {Class} instance(s) — module class not registered in this build` warning 加到 `summary.warnings`。

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

## 2026-08-11 — 导入公告栏到**新** team id

走 [[team_bulletin_transfer]] 的 `write_imported_bulletin`，落在 id map 铸出的新 team id 上。
bundle 是不可信输入：上限重新施加，且无论 payload 声称什么都不写自动总结。

## 2026-08-11 (review) — 回滚也要扫公告栏表

导入失败的回滚里，通用的 agent 表清扫只覆盖**带 `agent_id` 列**的表，
而 `team_bulletin_entries` 只有 `team_id` / `author_id`，两边都不命中。
于是 `write_imported_bulletin` 写进去的行会留在一个下一行就被删掉的 `team_id` 上——
**任何查询路径都读不到**，正是 `_wipe_team_data` 自己论证过的那种孤儿行。
#259 在紧挨着的一行把 `team_work_items` 加了进去，对比之下这个缺口才显出来。

## 2026-09-04 · services + host hooks (batch 3c.6)

Skill installs go through `skills.workspaces` (`utils/plugin_services.skill_workspace`); the renamed-agent identity fix fires `onDidSettleAgentName` on the importer's db and reads the reconcile result from the hook outcome.

## 2026-09-04 · credentials land in the generic store (batch 4d.2)

Both the preflight clash check and the landing loop iterate `channel_credential_tables.bundle_rows(payload)` (4d bundles and pre-4d per-table bundles alike). Clash = `GenericCredentialStore.find_one(channel, external_id=…)` already bound in this install → skipped, counted in `channel_credentials_skipped_conflict`. Landing = `rewrite_row("channel_credentials", row)` (agent_id remapped through `id_field_map`, IM-side owner ids untouched) then `store.upsert(channel, new_agent_id, values, enabled=False)` — invariant 1 (force-inactive) is now the `enabled` argument, not a per-table column name. A channel this install does not know (`UnknownChannel`, e.g. a plugin channel not installed here) is reported as a warning and skipped, never crashes the import.

## 2026-09-04 · `module_registry` replaces `MODULE_MAP` (batch 5d)

The registry view is the only module table; usages renamed.

## 2026-09-07 — preflight swallows only UnknownChannel

The credential-clash check caught bare Exception and reported 'no clash' on a DB error or an unreadable row — false consent. Only UnknownChannel (a channel this install does not know) is a non-clash; anything else propagates because preflight is a dry run and may fail loudly.
