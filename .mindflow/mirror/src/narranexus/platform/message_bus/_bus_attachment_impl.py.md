---
code_file: src/narranexus/platform/message_bus/_bus_attachment_impl.py
last_verified: 2026-10-28
stub: false
---

## 2026-10-28 — 工作区相对 ref 走 `resolve_inside_workspace`，workspace 根不再 resolve

`_resolve_ref_to_source` 原来 `agent_workspace_path(...).resolve()` 再 `is_relative_to`：发送方
把自己的 workspace 目录（在它自己的挂载里）换成指向别的租户 workspace 的链接后，`resolve()`
让对方目录成了"自己的 workspace"，`ref` 指向的对方文件就被硬链/复制进本用户的 bus_files，
再经消息投递读出来。现在根 = realpath(base) + 字面尾段，交给 [[workspace_paths]] 的
`resolve_inside_workspace`（拒绝链接根、候选 realpath 后必须落在字面根内）；ValueError 在这里
降级为"记 warning、该 ref 什么也不 stage"，与原来的越界处理一致。`att_` 句柄路径不受影响。
测试：`test_attachment_staging.py::test_symlinked_sender_workspace_stages_nothing`。

## 2026-09-13（PR#401 review 🟡2 → 第八轮）— marker 改走 `file_marker`，所有值编码，落盘后缀清洗

`build_bus_markers` 不再自己拼 marker，改调 `attachment_schema.file_marker`（与用户上传同一函数）。marker 里
**每个值**——文件名、path、mime、kind、发送者、transcript——都是 JSON 字面量（path 是无损的 `exact_literal`，
由 `root / rel_path` 拼出的路径逐字节回解；`category` 原样传入，枚举由 `file_marker` 还原），只有 `Shared file`、键名和结尾是
固定字面量；`mime_type` 可能是外部声明的 Content-Type，`category` 来自 dict，都不被当成可信平台值。
`from_agent` 现在是**原始**发送者（句柄或显示名），由 `file_marker` 编码成 `from="…"`，调用方不再预编码。
`_new_target` 收文件名并过 `on_disk_suffix`，换行/空格/逗号进不了落盘路径（纵深防御，不是这一行安全性的前提）。

## 2026-09-11（PR#401）— `build_bus_markers` 一个 marker 只占一行

文件名（`original_name`）和 transcript 由发送方写，可能带换行，原样拼进 marker 就能在团队房
scrollback 里开出一行 `User: …`。现在两者经 `inline_field(x, None)` 折叠空白（不加引号：marker 内部
形态与用户上传 marker `Attachment.synthesize_marker` 共享）。`from_agent` 按调用方给的原样打印——
团队房传已编码的标签，peer 私聊传句柄。

## 2026-09-09 — `_resolve_ref_to_source` 补上共享区回退（B-22，#122）

`att_...` 格式的 ref 只试过 `resolve_attachment_path`（发送方自己的
`user_upload_files`），从不试 `resolve_shared_file_by_id`（本文件后面就定义了它，
只是没人从这里调）。两个 store 的 file_id 都由同一个 `generate_file_id()` 铸造，
形状完全相同——区分不了「这是我自己传的」还是「这是团队群里别人（人类上传或另一个
agent 已 stage 过）传的」，唯一的办法就是两边都探一遍。

之前的行为：`team_share_file`（工具名；2026-08-19 条目记过的 `bus_*` 旧名早已退役，第一版又写回了旧名）转发一个团队群聊里的 `att_` 附件（人类在群里直接上传、
或另一个 agent 已经 stage 进这个团队的文件）到另一个团队时，`resolve_attachment_path`
在发送 agent 自己的 upload 目录里必然找不到，函数直接返回 `None`，ref 静默解析失败——
调用方 `resolve_and_stage_refs` / `stage_path_into_team` 都把 `None` 当作「这条 ref 跳过」，
所以症状是分享无声无息地丢了，没有任何错误。

修法：`is_valid_file_id(ref)` 命中后，先探 `resolve_attachment_path`（发送方自己的
store，最常见路径，保持不变），**miss 时**再探 `resolve_shared_file_by_id(owner_user_id,
ref, base)`。顺序很重要——发送方自己的 upload 优先，共享区只是兜底，不反过来。

**信任边界（复审 M4/M5，有意为之、记录在此）**：`resolve_shared_file_by_id(owner_user_id, ref, base)`
只用 `bus_files_dir(user_id)` 拼根、`file_id` 已过 `^att_[a-z0-9]{8}$`，结构性禁闭在
`{base}/{user_id}/_shared/bus_files` 内；`owner_user_id` 由工具从 `agents.created_by` 查出、
`agent_id` 被注入身份强制覆盖，模型操纵不了——**跨用户不可能**。同 owner 内确有扩面：共享区按
用户扁平（不按 team 分区），所以 agent A 现在能按 id 解析到它从未加入的团队群里人类上传的
文件、再经 `team_share_file` 转进它所在的团队。但 `utils/workspace_paths.py::turn_accessible_roots`
早已把整棵 `bus_files` 树无条件授予每个回合（注释明写 "user-wide by design"），这只是省掉了
"先 Read 再按工作区相对路径附上"一步，不是新的读取原语。工具 docstring（`message_agent` /
`team_share_file`）同批改成与实现一致（原来写"只能分享你实际拥有的文件"）。真正收口属于共享区
按 team 分区的改造，记 todo。

## 2026-08-19 — 文档指针刷新

注释里 `bus_list_team_files` → `team_list_files`（退役名）。纯注释。

## 2026-08-10 (review 修正) — 仓储 import 移到 `schema` 与 `utils` 之间

纯位置：上一轮把它插进了 `utils.*` 块中间，把成块的 import 劈成两半。ruff 只 select `["E","F"]`、
没开 isort，所以不会报——这属于纯手工维护的秩序。

## 2026-08-10 (review 修正) — 仓储 import 提到模块级

纯位置调整：`team_workspace_repository` 的 import 从函数体移到模块顶部。这里没有循环依赖要躲
（仓储层只 import `repository.base.parse_dt`），同一行写在多个函数里是纯噪音。

## 2026-08-10 (review 修正) — 去重竞态回读；探测返回改为具名元组

**竞态**：去重探测是 check-then-act 且无锁，而索引是 UNIQUE。两个并发的同内容分享都会 miss，
第二次 insert 撞约束。原来被 blanket except 吞掉 → **文件上了盘但没有任何行**，在面板和
`bus_list_team_files` 里永远不存在——这恰恰是该索引要防的那一件事。现在撞约束时**回读赢家那一行
并按「同内容重分享」返回**，调用方得到与顺序执行时相同的答案。

**探测返回值**从 `dict | str | None` 三态改为具名元组 `_DedupProbe(row, digest)`：调用方原本要
`isinstance` 才能还原语义，而 digest 在某一分支是裸 `str`，很容易被误读成「没匹配」。

**复用前确认文件仍在**：`_wipe_team_data` 会删目录却不动不属于它的行，指向空处的行会让 agent
拿到一条死路径却报成功。

## 2026-08-07 — 团队共享文件落库 + 内容哈希去重

`stage_path_into_team` 此前只落盘不落库：文件以生成的 `file_id` 命名，`original_name` 只活在
返回的 dict 里 → **共享目录无法被枚举**，只能靠 agent 在群里念绝对路径。现在写 `team_files`
索引行。

**去重是非对称的，这是关键**：
- 同名 + 同 hash → 真重复，复用既有行、**连拷贝都跳过**
- 同名 + 不同 hash → **两份都留**。折叠掉后者等于静默破坏性写入（新的顶掉旧的且无法找回），
  只按文件名判重就是在干这件事

**哈希成本的真实情况（初版设计文档在此处论断错误，已更正）**：新文件**必须**哈希一次，
否则 `content_hash` 无从写入、将来无从比对。`(team_id, original_name, size_bytes)` 前置探测
买到的是更窄的东西——一次分享**只读一遍源文件**，而不是对每个候选行各哈希一次。上界由 25MB
单文件上限约束，且跑在线程里。
（考虑过插入时留 NULL、碰撞时惰性回填：能让新文件真 O(1)，但唯一索引在 NULL 期间失去约束力，
用正确性换被上界封顶的性能收益，不划算。）

**索引失败只记日志不抛**：此刻文件已在盘上、按路径可达，为保住记账而让分享失败，是拿用户
真正要的结果去换账本。

## 2026-07-22 — meta sidecar + off-loop disk writes + public facade

- **Meta sidecar (`{file_id}_meta.json`)**: `store_bus_attachment_meta` /
  `load_bus_attachment_meta` persist the server-built attachment dict next to
  the staged file. Written by the team-chat upload endpoint after Whisper
  finishes; at send time the route reloads THIS instead of trusting the
  client's echoed dict (transcript is raw prompt injection otherwise — PR
  #141 review). Underscore (not dot) in the name keeps the sidecar invisible
  to `resolve_shared_file_by_id`'s `{file_id}.*` glob so it can never shadow
  the original.
- **Disk writes moved off the event loop**: `_stage_into`'s link/copy and
  `store_bytes_into_bus`'s `write_bytes` (uploads up to 50 MB) run via
  `asyncio.to_thread`; `store_bytes_into_bus` is now async — callers await it.
- **Cross-package consumers import the [[attachments]] facade**, never this
  module directly (routes/MCP tools were pinned to the private impl).

# _bus_attachment_impl.py — 让 bus 消息携带文件

## 为什么存在

message bus 原本只传纯文本。这个私有实现让 Agent 之间能通过 bus 互发文件/图片
（多模态 A2A）。它不把字节塞进消息，而是**按引用传**：发送时把文件落到"每-user
共享区"，消息里只存一小段元数据 + base 相对路径；投递时由 trigger 渲染成 Read 工具
marker，recipient 用内置多模态 `Read` 直接读。

## 成立的前提（关键洞察）

两条约束咬合让"跨 agent 传文件"变简单，无需 per-recipient 拷贝：
1. bus **只允许同 user**（`local_bus` 跨 user `PermissionError`）；
2. Executor 按 **per-user 挂载** `{base}/{user_id}`（见 [[workspace_paths]]）。

所以同 user 下所有 agent 共享一个文件根，共享区 `{base}/{user_id}/_shared/bus_files/`
对每个 recipient 都物理可见 + 可读（沙箱验证见设计文档 §1.3：codex `"**":"read"` /
claude `bypassPermissions`）。**本地与云端同一条码路**，不分叉——这也是刻意对齐铁律 #7。

## 上下游

- **被谁调用**：`_message_bus_mcp_tools.py`（`message_team` / `message_agent`
  的 `attachment_refs` → `resolve_and_stage_refs`；`team_share_file` → `stage_path_into_team`）；
  `message_bus_trigger.py` 两个 prompt builder → `build_bus_markers`。
- **依赖谁**：`attachment_storage`（file_id 解析 + `generate_file_id`）、`workspace_paths`
  （`agent_workspace_path` / `bus_files_dir` / `team_shared_dir`）、`file_safety`
  （`ensure_within_directory` 防逃逸）、`attachment_schema.derive_category_from_mime`。

## 设计决策

- **句柄两类**：`att_` 前缀走 `resolve_attachment_path`（发送方 user_upload_files）；
  否则当作发送方 workspace 相对路径，`is_relative_to(workspace)` 拒绝 `../` 逃逸与绝对路径。
- **落地 hard-link 优先**：`os.link` 零复制，任何 `OSError`（EXDEV 跨设备等）fallback
  `shutil.copy2`。大文件转发不翻倍占盘。
- **存 base 相对路径**（`rel_path`），marker 构建时 join `base_working_path` → 绝对。
  抗 base 漂移，与 `instance_artifacts.file_path` 同思路；**不复用**
  `Attachment.synthesize_marker`（那是 user_upload_files 专用解析），而用 `build_bus_markers`
  产出**同格式** marker（`… — use Read tool to view]`），保持 recipient 行为与 user 上传一致。
- **marker 无需 recipient 的 agent_id/user_id**：路径已是共享区绝对路径，不再二次解析——
  天然绕开 group channel 多 recipient 的 per-recipient staging 难题。

## 2026-07-21 — voice memos: transcript in marker + by-id resolver

`build_bus_markers` now appends `transcript=…` when a bus-attachment dict carries one, so a
recipient agent reads a voice memo's words inline. Added `resolve_shared_file_by_id(user_id,
file_id)` — globs `_shared/bus_files/*/{file_id}.*` (prefers the original over an `.mp3`
transcode sibling), used by [[transcription_public]] as the fallback when the agent-scoped
resolver misses (team voice memos live in the shared area, not user_upload_files).

## 2026-07-21 — store_bytes_into_bus (user uploads)

Added `store_bytes_into_bus(user_id, raw_bytes, original_name, mime_type)` — the upload
counterpart to `resolve_and_stage_refs`. A human attaching a file to a team message has no
source workspace file to reference, so this writes the raw bytes straight into
`{base}/{user_id}/_shared/bus_files/{date}/` and returns the same bus-attachment dict.
Shared dict-building/target helpers (`_bus_att_dict`, `_new_target`) were factored out so
the link-from-file path (`_stage_into`) and the write-bytes path share one shape. Called by
`teams.py`'s team-chat upload endpoint. MIME is passed in (server-sniffed by the route),
not guessed here.

## Gotcha

- `resolve_and_stage_refs` **永不抛**：单个坏引用 log warning 后跳过，不能因一个附件
  失败而丢整条消息（与铁律：附件是增强，不是消息主体）。
- `sender_agent_id`/`owner_user_id` 必须来自运行时认证上下文，**绝不接受 LLM 传参**
  （与 attachment_storage 同规约）。MCP 工具里 owner 由 `agents.created_by` 反查，不信任 LLM。
- 共享区**无 `_index.json`**：DB 里的 attachment dict 就是索引，rel_path 直接可解析。
- team 共享区写入必须走服务端工具：cloud 沙箱下 agent 只能写自己 workspace，`_shared`
  对 agent 是 read-only，所以 `team_share_file` 由（非沙箱的）MCP server 进程代写。
