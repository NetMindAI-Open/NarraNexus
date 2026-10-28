---
code_file: src/narranexus/platform/utils/workspace_paths.py
stub: false
last_verified: 2026-10-28
---

## 2026-10-28（PR #421 复审）— `_candidate_relpaths` 改为公共名 `workspace_candidate_relpaths`

[[confinement]] 的 `owner_roots` 把三种布局形态都当作该 agent 的合法根，跨模块依赖它；
私有名会让改签名的人在本模块里 grep 不到外部调用点。三处内部调用同步改名，无别名
（铁律 #2）。改它的返回集合 = 改 artifact 能从哪些根被服务/编辑/检测。

## 2026-10-28（二审）— `resolve_inside_workspace`：主机侧解析 agent 给的路径只走这一处

主机侧代码要把 agent 提供的路径（相对 workspace 或绝对）变成要读/复制/发送/监视的文件时，
统一调用 `resolve_inside_workspace(workspace, candidate)`：先 `refuse_symlinked_dir(workspace)`，
再 `(workspace / candidate).resolve()`（绝对路径照旧直接胜出），必须 `relative_to(workspace)`，
否则 ValueError；各调用方再包成自己的错误类型。**workspace 只做 `abspath` 规范化、绝不 resolve**，
所以调用方必须用 realpath 过的 base 构造它（`resolve_existing_workspace` 已如此；
`agent_workspace_path` 要显式传 `base=os.path.realpath(...)`），否则 base 本身是平台级链接
（macOS `/var`）时候选永远落在根外。即使把显式拒绝链接那一步拿掉，字面根也足以把
realpath 后落进受害者目录的候选挡在外面——拒绝那一步只是让错误更明确。

接入点（各自保留原错误类型，各有一条"workspace 换成指向别的用户目录的链接"测试）：
[[_bus_attachment_impl]] 的工作区相对 ref（→ 记 warning、不 stage）、
[[_matrix_send]] 的 `resolve_workspace_file`（→ `MatrixSendError("bad_path")`）、
[[skill_backup]] 的 `archive_local_zip`（→ ValueError）、[[office_watch]] 的
`resolve_watch_file`（→ ValueError）与 `ensure_watch`（→ 返回 None）。backend
`agents/files.py` 的 `_resolve_within_workspace` 是同一口径的内联写法（字面根、realpath 候选）。
[[heal]]（PR #421 复审后从"豁免"挪到这里）：scope 根（workspace 或 team 目录）字面构造、
是链接就直接返回空结果；每个候选在 stat / 哈希 / "指针已有效"判定前都经
`resolve_inside_workspace` 确认仍在根内（→ 丢弃该候选）。

## 2026-10-28（复审后）— base 只做一次 realpath，租户尾段永远字面；新增 `refuse_symlinked_dir`

`resolve_existing_workspace` / `_shared_base` 对 **base**（平台所有）做 realpath，之后的
`{user}/{agent}`、`_shared/teams/{team}` 尾段只字面拼接。原因：这些目录在租户自己的挂载里，
租户可把它们换成指向别人目录的链接；对根做 realpath 等于把链接目标认作自己的根。
artifact 注册/限定（[[confinement]]、[[registration]]、[[raw_access]]）、files 路由、导出打包
以及上面的五个接入点都是"根字面、entry realpath、落在根内才算"，并用 `refuse_symlinked_dir`
直接拒绝链接根。

**仍 resolve 根、且有意保留的调用方**（不是遗漏）：
- backend `routes/manyfold/files.py::_safe_resolve` —— 凭运维网关 token 访问，按设计可寻址
  任意 agent，不存在"租户越权"面。
- NexusPower `policy.py` / `files.py` —— 运行在 executor 容器里，那里只挂了本用户的根，
  指向主机路径的链接在容器内是悬空的。
- `migration/applier.py::_copy_local_skill` —— 仅本地/桌面的 `POST /migrate/apply`（云端 503），
  单一属主，agent 调不到，见 [[applier]]。
- `_bus_attachment_impl.resolve_shared_file_for_user` 对 `{base}/{user_id}` 的 resolve —— 那是
  bind mount 本身的挂载点，租户在容器内无法把它换成链接。

## 2026-10-28 — `safe_id_segment`：id 永远只能是一个目录名

跨租户读事件后新增。`agent_workspace_relpath` / `user_shared_root` / `team_shared_dir`
在拼路径前都先过它：空、含 `/` `\\` NUL、以 `.` 开头（覆盖 `.` `..` `...` `..;`
`.hidden`）一律 `ValueError`。它是**路径安全**栅栏，不是 id 形状校验——磁盘上真实存在
带空格的 user_id 和 `agent_a_abb17376` 这类 agent_id，必须继续解析，所以不能上严格
正则。`workspace_candidate_relpaths` 经由 `agent_workspace_relpath` 自动受保护，因此
`resolve_existing_workspace` 和 `resolve_workspace_relative_file` 的 **id 参数**也一并覆盖。
注意 `resolve_workspace_relative_file` 的 `file_path` 参数仍是原样 join（绝对路径会直接
胜出），其范围限制一直由调用方（artifact raw_access 等）负责，本次未改。

HTTP 入口不应依赖这里的 ValueError 变成 500：面向 workspace 的路由（files / attachments /
skills）先用 [[_ownership]] 的 `require_owned_workspace_agent` 做确权与 404。这里是给
30 个非路由调用方（bus 附件、bundle、artifact、executor…）的最后一道兜底；未经路由
确权直接到这里的坏 id 会以 ValueError 冒泡（executor `/watch/ensure` 这类内部端点上
表现为 500，可接受）。common_tools 的 `artifact_lines` 曾以空 user_id 调用它，已改成
没有 user 就不生成本工作区前缀（[[artifact_lines]]）。

## 2026-08-10 (review 修正) — 新增 `turn_accessible_roots`

一个回合在**自己 workspace 之外**能碰到的根，收口到一处。两项性质不同：

- `bus_files` — **按 user 授予是设计如此**：bus 把附件 stage **一份**到 owner 的共享区，
  同 user 的每个接收方读的就是那一个路径。按 team 收窄会直接打断消息投递（验收 #6 明确
  包含 bus 附件路径）。
- `teams/{team_id}` — **只给本回合所属的那一个 team**。

**父目录 `_shared` 永不返回**：给了它等于用一个入口把该 owner 的每个 team 又放回来。

初版就是那样：每个回合都授予整棵 `_shared`，包括压根不属于任何 team 的一对一私聊回合。
这与本功能其余部分自相矛盾——[[registration.py]] 的 `_resolve_entry` 只放行本回合那一个
team，[[artifact_repository.py]] 的 `list_for_agent_context` 特意 join `team_members` 而非
按 owner，注释还写明按 owner 取就是 cross-team leak。确权层当时用的正是 owner 口径。

**「accessible」不是「readable」**：消费它的 confinement 层检查 `file_path` 与 shell 路径，
所以同一份授予同时管 Write / Edit / rm。

## 2026-07-20 — per-user shared-area helpers

Added `user_shared_root` / `bus_files_dir` / `team_shared_dir`, all rooted at
`{base}/{user_id}/_shared` — a SIBLING of each agent's own workspace dir, deliberately
not inside one. Because the per-user Executor bind-mounts the whole `{base}/{user_id}`
subtree, these dirs are Read-able by every same-user agent in both local and cloud
mode. This is what makes cross-agent file sharing on the bus work without copying into
each recipient's workspace (see [[_bus_attachment_impl]]).

## Why it exists

Single source of truth for an agent's on-disk workspace layout. The
layout `{base}/{agent_id}_{user_id}` used to be hardcoded as
`f"{agent_id}_{user_id}"` in ~11 places (step_3, bundle builder/importer/
skill_backup, bootstrap, skill_module, attachment_storage, the artifact subsystem (narranexus/platform/artifact),
arena_provisioning, identity_migration). This module centralizes it so the
layout can change in ONE place.

## Layout switch

`_LAYOUT` selects the on-disk shape:
- `"flat"` — legacy `{agent_id}_{user_id}`.
- `"nested"` — `{user_id}/{agent_id}` (**current**). This is what lets a
  per-user Executor container bind-mount only `{base}/{user_id}` and thus
  see ONLY that user's agents — cross-user file isolation by mount, no uid
  tricks (the P2 plan, binding rule #20 data-plane).

Flipped flat→nested on 2026-06-17 together with the migration below. All
call sites route through `agent_workspace_relpath` / `agent_workspace_path`,
so the flip was a one-line change here.

## Migration (`migrate_flat_to_nested`)

One-off, idempotent, non-destructive (rename only; never overwrite/delete).
CLI: `scripts/data_migrations/migrate_workspace_layout.py` (dry-run default, `--apply`).

**Disambiguation gotcha (why it takes `known_user_ids`):** a flat dir
`agent_<hex>_<rest>` is ambiguous — `<rest>` could be the user_id directly,
OR the legacy `_user_` infix form (`agent_x_user_binliang` = user
`binliang`, not `user_binliang`). Dir names alone can't tell them apart, so
the migration resolves `<rest>` against the authoritative set of real user
ids from the DB `users` table. Dirs whose owner doesn't resolve are
reported as `unknown` and **left in place — never guessed** (avoids
creating bogus user dirs). Verified on real data 2026-06-17: 284 moved,
0 conflicts, 53 unknown orphans safely left.

## Reader fallback resolvers (avoid a DB rewrite)

The dir migration moves files, but DB columns that store a workspace path
WITH the prefix (notably `instance_artifacts.file_path`, base-relative like
`agent_x_user_y/work/o.html`) are NOT rewritten. Rather than a risky DB
migration (binding rule #6), READERS of existing data use:
- `resolve_existing_workspace(agent_id, user_id, base)` — the workspace dir
  that EXISTS, current layout first then legacy flat / `_user_` fallback.
- `resolve_workspace_relative_file(file_path, agent_id, user_id, base)` —
  resolves a stored base-relative-with-prefix path to a file that exists,
  swapping the prefix flat↔nested if needed.

Wired into every hardcoded flat site the nested flip would otherwise break
(binding rule #8 sweep): `artifacts/public.py`, `agents/artifacts.py`,
`agents/files.py`, `manyfold/files.py`, `auth.py` (workspace delete + the
THREE `bootstrap_active` checks: GET agents, update agent, create agent),
`common_tools_module.py` (artifact list display), `context_runtime.py`
(Bootstrap.md path → bootstrap_active gate), and `_social_mcp_tools.py`
(sub-agent workspace create). So both old (flat) and new (nested) rows
resolve — no DB rewrite, works through the transition forever.

**Why the bootstrap ones mattered:** `apply_bootstrap` writes `Bootstrap.md`
to the nested path, but `bootstrap_active` was checked at the flat path in
3 places → the gate read False → the new-agent greeting + "read Bootstrap.md
and introduce yourself" prompt silently vanished. The audit first missed
these (they key off `created_by`/`owner_user_id` + multiline joins).

## Gotchas

- `_LAYOUT` must be flipped to `"nested"` ONLY after the migration has run
  on a base, or running agents lose their workspace.
- Agent ids are `agent_<hex>` (single token, no internal `_`) — the parse
  relies on this.
- run.sh uses `BASE_WORKING_PATH=/data/workspaces`; the settings default is
  `~/.nexusagent/workspaces` — migrate whichever base a given deploy uses.
- deploy: to bind-mount per `{user_id}`, `workspaces` must be a host
  dir / volume-subpath, not an opaque named volume (deploy-repo change).
