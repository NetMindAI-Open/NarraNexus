---
code_file: src/narranexus/platform/artifact/_artifact_impl/heal.py
last_verified: 2026-10-28
stub: false
---

# heal.py — broken-pointer recovery strategy

## Why it exists

Under the pointer model an artifact row can outlive its on-disk entry file
(agent moved/deleted the file, legacy NULL-file_path rows, register killed
mid-flight). The raw route answers 410 for such rows; the frontend
([[useArtifactHeal.ts]]) calls heal to reconnect the pointer.

Extracted 2026-07-21 from the `agents/artifacts.py` route handler (where the
whole strategy lived inline) so it is plain, testable service logic
(`tests/artifact/test_heal.py` covers every branch) instead of HTTP-handler
body.

## The strategy (each step short-circuits)

1. **Pointer re-check** — file actually on disk → recovered, no write. Handles
   transient-410 races.
2. **Caller-picked path** (`entry_path` given, the "user picked from the
   modal" flow) — re-register onto the same artifact_id. Rejections propagate
   as `ArtifactError` so the caller sees the cause.
3. **Scan by kind** (`_KIND_EXTENSIONS`, mtime desc, capped at
   `_HEAL_MAX_CANDIDATES`=10): unique match → auto-register; 0 / >1 →
   `recovered=False` + candidates for the modal.

All three steps read one root, `search_root` — see the 2026-08-10 entry.

All re-registrations go through [[registration.py]] with
`target_artifact_id` set, so kind whitelist / path confinement / size cap stay
identical to every other registration path.

## Design decisions

- Returns `HealResult` (schema model, doubles as the route's response_model) —
  the "not recovered" outcomes are data, not exceptions; only "artifact does
  not exist / not yours" raises (`ArtifactNotFound`).
- The scan does not follow symlinks into directories, and every file it sees is
  confined (`_confined` → `resolve_inside_workspace`) BEFORE its size/mtime is
  read; see the 2026-10-28 entry. Register-time realpath confinement is a second
  gate, not the only one.
- `application/vnd.officecli-live` maps to (.pptx, .docx, .xlsx) so heal works
  for office artifacts too (2026-07-13 behavior, carried over).

## 2026-10-28（PR #421 复审 I2）— scope 根字面、候选逐个确认在根内

之前 `search_root` 是 realpath 过的 workspace / team 目录，并被列为"只产出候选、有意豁免"——
这个理由不成立：heal 在产出候选前就已经**动了那棵树**。租户把自己的 workspace（或 team 目录、
或其中一层父目录）换成指向别人目录的链接后：
- 第 1 步会把受害者同一相对路径上的文件判成"pointer is already valid"；
- 第 3 步把**受害者的文件名**装进 `HealResult.candidates` 回给前端；
- hash 层**逐个读受害者文件字节**与 `art.content_hash` 比对——按内容猜测确认的通道。
`_repoint` 最终会被注册闸口挡住，但清单和哈希已经出去了。

现在：`search_root` 用 realpath 过的 base + 字面租户尾段构造；**是链接就直接返回**
`recovered=False, candidates=[]`（不 scan、不 hash、不 repoint、消息不含目标路径）；
`_scan_workspace_for_kind` 字面遍历、每个文件先 `_confined` 再 `os.stat`（指向根外的文件链接、
或被换成链接的父目录，既不贡献名字也不贡献 size/mtime）；第 1 步的"已有效"判定、"别抢别人
指着的文件"护栏、hash 层都用 `_confined` 的 realpath，hash 层在读字节那一刻再确认一次
（扫描后被换掉的路径也读不到）。

护栏口径：候选侧是 `_confined` 返回的 realpath，taken 侧仍是 `realpath(join(base, fp))`——
两侧都是 realpath 才一致；只改一侧会在工作区内存在链接时失配，把同一文件分给两个 artifact。
测试：`test_heal_refuses_a_workspace_swapped_for_a_link`、
`test_heal_skips_a_file_link_that_points_outside_the_workspace`、
`test_a_team_folder_swapped_for_a_link_is_not_scanned`。

## 2026-08-10 — the root is the artifact's, not always the agent's

Every step above was written when an artifact could only live in the producing
agent's workspace, and all three broke silently once [[registration.py]] began
REQUIRING a team artifact to live in `_shared/teams/{id}`:

- step 1 compared an intact team pointer against the workspace root, so a
  perfectly healthy artifact was declared broken and the flow continued;
- step 3 walked that same workspace, so the modal offered the agent's unrelated
  private files as replacements for a team artifact;
- both re-registrations omitted `team_id`, so they failed the reachability
  check added alongside it and surfaced as "artifact not found".

Nothing here reported an error — it reported the WRONG ANSWER at each step,
ending in a failure whose message named the wrong cause. The three now derive
one `search_root` (team folder when `art.team_id`, workspace otherwise) and
pass `team_id` through.

`_absolutise` exists because candidates are reported relative to the scan root
while `_resolve_entry` deliberately resolves a relative `entry_path` against
the agent's own workspace — it refuses to re-base one onto a root the agent did
not name. That rule is right for the tool surface, so heal names the root
explicitly rather than weakening it.

This was cited in [[teams]] as a reason `clear_files` must cascade to
artifacts. The cascade is still right, but for the other reason given there:
heal only ever RECONNECTS a pointer to a file that still exists, and a wipe
deletes the files.

## 2026-08-18 — hash 认亲层 + 两道护栏 + 重指诚实化

候选流水线变为:扫描 → **排除其他活 artifact 的当前指向**(绝对 realpath 比较;
否则两个 artifact 收敛到同一文件,改一个动两个)→ **hash 层**(表里有 content_hash
时逐候选验 sha256:恰 1 命中=验明正身确定性重指;≥2 命中=复制品意图不明,交弹窗;
0 命中=改名且改了内容,退扩展名层)→ 扩展名层(现状:1 自动/0 失败/N 弹窗)。

**一切重指走 `_repoint` 单一出口**(含用户弹窗选定):history action="healed"
(register 的 history_action 参数)+ suppress_notify 抑制普通 updated 事件 +
自己 stage 更富的 "repointed"(extra: old/new 路径尾+hash_matched)。语义:
猜测/验证永不伪装成有意更新;前端据 repointed 弹 toast 并立即重载。
声明边界:hash 验内容不验意图(可能命中用户备份,toast 的路径是最后防线)。

## 2026-08-20 — 候选哈希下线程(#334 I12)

hash 认亲一次可能连读十几个候选文件,整段 to_thread;语义不变。
