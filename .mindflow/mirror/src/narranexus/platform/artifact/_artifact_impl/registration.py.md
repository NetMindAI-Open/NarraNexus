---
code_file: src/narranexus/platform/artifact/_artifact_impl/registration.py
last_verified: 2026-10-28
stub: false
---

## 2026-10-28 — 注册时 workspace/team 根为字面路径且拒绝符号链接

`_resolve_entry`（及同形的第二处）原来 `realpath(workspace_root)`：租户把 workspace 目录
换成指向受害者目录的链接后，`realpath` 让受害者目录成了"自己的 workspace"，注册出的行
`file_path` 直接指向别人的文件。现在根是 realpath(base)+字面尾段，根本身是链接直接
`ArtifactPathEscape`；entry 仍做 realpath，链接一出现就落到根外。team 根同样处理。

`_relative_to_base` 同样对 base 做 realpath：entry 是 realpath，base 若是平台级链接（macOS
`/var` → `/private/var`）而不 realpath，存进库的 `file_path` 会带一串 `../`，之后
[[confinement]] 解析出来就落在属主根外。

## 2026-08-10 (方案 B 的后果修正) — 团队根也是「容器根」

`_resolve_entry` 现在返回 `(abs_entry, artifact_root, team_root)`，体量核算把
`artifact_root in (workspace, team_root)` 一并当作**单文件模式**。

**为什么必须改**：把团队 artifact 要求进团队目录之后，最常见的落点让 `artifact_root` **就是团队
根**，于是 `_dir_size` 会按「该团队分享过的一切」计费——一旦越过 `MAX_ARTIFACT_BYTES`，**团队里
谁都再也注册不了任何东西**。这是方案 B 引入的，不是既有问题。

`team_root` 由这里返回而非调用方重算：同一个事实算两遍，迟早会有一处漏改
（[[raw_access.py]] 有同源的第二处，见那份 md）。

顺带把 `team_shared_dir` 的 import 提到模块级——上一轮声称「0 残留」时漏了这一处。

## 2026-08-10 (review 修正) — 仓储 import 提到模块级

纯位置调整：`team_workspace_repository` 的 import 从函数体移到模块顶部。这里没有循环依赖要躲
（仓储层只 import `repository.base.parse_dt`），同一行写在多个函数里是纯噪音。

## 2026-08-10 (方案 B) — 团队 artifact **必须**住在团队目录

此前是「workspace **或** 团队目录都允许」。现在按归属分岔：私有 → 自己 workspace；
**团队 → 只能是那个 team 的共享目录**。

**理由是可达性，不是整洁。**队友的回合只被授予三个根（[[workspace_paths.py]] 的
`turn_accessible_roots`）：自己的 workspace、bus 附件目录、本回合 team 的目录。留在**生产者
workspace** 里的文件三者皆不在——NexusPower 直接 DENY，而 claude/codex 无此层却能读。这正是
本功能要消灭的「三框架两种行为」，并且会**静默**击穿验收 #3（队友接力）。

指针语义未变：仍然不复制不移动。只是 entry 必须**已经**在团队读得到的地方，而 agent 有权写
那里（授予覆盖写），所以错误信息点名目录、一次移动即可重试成功。

配套：工具描述与 team prompt 都改成「团队里要写进共享目录」，否则 agent 按旧习惯写自己
workspace 会直接撞上这条拒绝。

## 2026-08-10 (review 修正) — target 分支补归属校验（含一个既有漏洞）

`target_artifact_id` 分支此前 `get_by_id` 之后**只校验 kind**，不校验 agent / user / team。
两个后果，一个是既有漏洞、一个是 team 功能引入的：

- **既有**：任何 agent 猜到一个 `art_` id 就能把别人的 artifact 指针改到自己的文件上。
  id 是 8 位 hex——猜从来不是难点，是**根本没人在查**。
- **本 PR 引入**：这条路径完全丢弃了服务端的 team 事实，于是团队回合里对私有 artifact
  传 target 会「成功」，但它永远不出现在团队面板里，agent 拿不到任何信号。

现在校验的是**可达性**，规则与读侧三界面完全一致：
- **团队 artifact** 属于团队 → 该团队的**任何**回合都可更新（接力是共享工作台的全部意义，
  agent 身份**刻意不是**判据）
- **私有 artifact** 属于产出者 → 只有它自己能更新

因此团队回合够不到私有 artifact，私有回合也够不到团队的——否则 `scope="private"`
（本应只能**收窄**）就成了把 artifact 从所属团队里**拽出来**的手段。

拒绝一律用 `ArtifactNotFound`（404 形状），与 HTTP 路由一致：单独的 "forbidden" 会向探测者
确认哪些 id 存在。

## 2026-08-07 (三次) — 归因行写入 turn 句柄

`_record_history` 落 `event_id`。此前该列恒为 NULL（schema 阶段有意推迟）。

**为什么不靠时间戳推断**：同一轮产出两个 artifact、或同房间并发回合，按时间近邻匹配都会错。
turn 是平台**持有的事实**，传进来即可，不必猜。

## 2026-08-07 (二次) — scope 必须进去重键

agent-scoped 去重（`session_id is None` 分支）原本按 `{agent_id, file_path}` 匹配。加了 team
维度后这条键**不再唯一标识一个 artifact**：同一个文件在私聊和团队各注册一次是很普通的用法
（先给自己看，后来想给团队看），而旧键会让第二次静默返回第一次的行、**丢弃本次请求的 scope**。
两个方向都会串——私聊调用拿回团队 artifact，或团队注册被折叠进私有的、团队里没人看得见。

**scope 是身份的一部分，不是它的细节。**匹配条件补上 `existing.team_id == team_id`。

发现方式值得记：**单元测试全绿也没抓到**——每个用例用独立 DB、不同文件名，碰撞根本不会发生。
是端到端探针（真实 MCP 传输，连续四次调用同一个文件）暴露的。教训是同类「按业务键去重」的
逻辑，测试必须显式构造**同键不同维度**的碰撞，而不是依赖用例天然隔离。

去重本身存在的理由（2026-06-30 重复 Welcome tab 事故）未受影响，同 scope 内仍然合并，有
`test_dedup_still_works_within_one_scope` 守着。

## 2026-08-07 — team 归属、共享目录可注册（缺口 T6）、归因历史

**归属**：`register_artifact` 增加 `team_id`（None = 私有）。它来自服务端身份 header，
**不是**模型参数——见 [[artifact_tool.py]]。

**放开路径（缺口 T6）**：`_resolve_entry` 此前把 entry 硬限制在 agent 自己 workspace 内，
而共享目录按设计是每个 agent workspace 的 **sibling**（谁都不拥有它）→ 放进共享目录的文件
**永远无法注册成 artifact**。「共享目录里的东西不能变成团队可见产出」，那这个共享目录只是
半个功能。现在额外允许**本回合所属那个 team** 的目录：兄弟 team 的目录仍然越界；且**相对
路径依旧只相对自己 workspace 解析**，团队目录只能用绝对路径抵达，不会把相对路径悄悄重定基
到 agent 没有指名的根上。

**归因历史**：三条写入路径（新建 / target 重注册 / 同 entry 去重）各追加一行
`instance_artifact_history`。`_record_history` **永不抛异常**——此刻 artifact 本身已经正确，
让一条日志失败去毁掉一次成功注册，是拿 agent 的真实工作换记账；缺行只是历史降级，抛异常
才是功能降级。

## 2026-07-23 — agent-scoped re-register dedup

New-artifact path (no target_artifact_id) with `session_id=None` now checks
for an existing PINNED row with the same (agent_id, file_path, kind) and, if
found, updates that row's pointer/title and returns its id instead of
inserting. Rationale: the LLM tool never knows a session_id, so every
re-register of the same entry file minted another pinned tab that lived
forever (prod: 2× "Welcome to NarraNexus"; dev: 3× briefing pages).
Session-scoped registrations keep the old semantics. Kind mismatch on the
same path falls through to a new row. Pre-fix rows are cleaned once by
[[cleanup_duplicate_pinned_artifacts.py]].

## 2026-07-22 — application/x-url added to ALL_KINDS

`URL_ARTIFACT_KIND` ("application/x-url") joined the whitelist so URL tabs
register through the same pointer path as everything else. Their entry file is
a JSON doc written by [[url_artifact.py]] before registration; no other change
to this module.
## 2026-07-21 — moved out of common_tools_module (was artifact_runner.py)

This file is the old
`module/common_tools_module/_common_tools_impl/artifact_runner.py`, promoted
into the dedicated `narranexus/platform/artifact/` package. Logic is unchanged;
the exception classes moved to sibling [[errors.py]], and `_workspace_root`
became public `workspace_root` (heal shares it). History below is inherited
from the artifact_runner mirror.

# registration.py — pointer registration for artifacts

## Why it exists

The agent produces visual deliverables (ECharts JSON, HTML apps, CSV, Markdown,
images, PDFs) by **writing files into its own workspace** — that is its natural
working mode, and it lets a deliverable be multi-file (an entry `index.html`
plus `style.css`, `app.js`, `data.json`, images).

`register_artifact` is the bridge that makes such a workspace file *visible to
the user*. It does not write, copy, or move anything — it validates the entry
path, sizes the artifact root directory, sanity-caps it against
`MAX_ARTIFACT_BYTES`, and writes/updates one `instance_artifacts` row. Content
stays in the workspace; the backend serves it straight off disk.

## The model

- **artifact = entry file + its directory.** `artifact_root = dirname(entry)`.
  The whole root directory is served, so the entry HTML can reference siblings.
- The entry may sit anywhere inside the workspace — including the workspace
  root (single-file mode: size counts only the entry, and the serving layer
  refuses sibling requests so other workspace files stay private; see
  [[raw_access.py]]). Sibling-asset support is opt-in by putting the entry in
  a dedicated subdirectory.
- `target_artifact_id` re-registers onto an existing row (overwrites the
  pointer + title/description in place). Kind must match.

## Upstream / Downstream

- **Called by**: `ArtifactService.register` only (MCP tool, manual-register
  route, heal, and bootstrap all arrive through the service).
- **Depends on**: `ArtifactRepository` (DB I/O), `settings.base_working_path`
  (workspace root), `Artifact` / `ArtifactKind` / `CreateArtifactToolResult`.
- **Deliberately does not depend on**: agent_runtime, NarrativeService, any
  Module — it is a generic subsystem, not scenario-bound.

## Design decisions

- **`realpath` for the path-escape check.** Resolves symlinks: a
  workspace-interior symlink pointing at `/etc/passwd` is still rejected by the
  `startswith(workspace + os.sep)` test. `abspath` alone is not enough.

- **`size_bytes` is the recursive root directory size** (entry-file size only
  in workspace-root single-file mode) — stored for UI / debugging; nothing
  enforces a budget against it.

- **`MAX_ARTIFACT_BYTES` (25 MB) caps a single artifact** as a runaway guard.
  No per-user aggregate cap (removed 2026-05-19) — the agent's workspace
  already bounds disk usage, and the user owns the workspace.

- **No filesystem writes at all.** The only side effect is the DB row.

- **Office extensions override the caller's kind** (2026-07-13): entries
  ending in .pptx/.docx/.xlsx are forced to `OFFICE_LIVE_KIND` (imported from
  `utils/office_watch`, single source) — enables office-as-artifact and
  prevents registering a pptx as text/html.

## Gotchas

- `entry_path` may be absolute or workspace-relative — `_resolve_entry` joins
  relative paths against the workspace root before `realpath`.

- The artifact content is **live**: it points at the agent's real file. If the
  agent later edits or deletes the file/folder, the artifact changes or 410s.
  This is intentional (the whole point of the pointer model).

- `settings.base_working_path` is read at call time, not cached at import — so
  tests can monkeypatch it.

- The DB `file_path` is relative to `base_working_path`, so moving the
  workspace only needs a settings change; stored paths still resolve.

## Inherited history (from artifact_runner.py.md)

- **2026-05-19 — per-user quotas removed.** The per-user count/bytes quotas,
  `_enforce_quota`, `ArtifactQuotaExceeded`, and the repo quota helpers are
  gone. `MAX_ARTIFACT_BYTES` stays as the only cap.
- **2026-05-14-r3 — "must be in subdirectory" hard rule dropped.** With
  `delete_source`/rmtree gone, workspace-root entries became legal; exposure
  is prevented by soft-degrading at the serving layer instead.
- **2026-05-14 — rewritten for the pointer model.**
  The old copy/version model (create_text_artifact / upload_binary_artifact /
  version rows) collapsed into the single `register_artifact`.

## 2026-08-18 — `compute_entry_hash`:注册时给 entry 盖内容指纹

sha256(entry 文件本体),写进 `instance_artifacts.content_hash`。消费方是 heal 的
hash 认亲层(改名未改内容→确定性重指,不再按扩展名猜)。**best-effort 契约**:任何
IO 失败返回 None、warning 一条、注册照常——指纹是增强不是门槛。新注册与
target 重注册两条路径都盖;重注册直写新值(哈希失败时写 NULL 而非保留旧值,
过期指纹比缺失更危险)。

## 2026-08-18(二)— target 分支的 history_action / suppress_notify

仅作用于 target 重注册分支,heal 专用:healed 入史 + 抑制 updated 事件(heal 自己
stage repointed,避免双事件)。新建/去重分支不受影响。

## 2026-08-20 — register_artifact 增必填 `db` 形参(#334 r2 C3)

与 [[base.py]] 的「`db` property 删除」是同一契约的两端:事件 staging
的 db 由调用方显式传入(service/heal/open_url 均已改)。三处
stage_artifact_event 全部改用形参 db。
