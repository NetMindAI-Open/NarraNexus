---
code_file: src/narranexus/platform/artifact/_artifact_impl/raw_access.py
last_verified: 2026-10-28
stub: false
---

## 2026-10-28（PR #421 复审 I4）— 单文件模式的容器根 = `owner_roots`

容器根原来是 `resolve_existing_workspace`（只取**第一个存在**的 workspace 目录）∪ team 目录，
而 `confine_entry` 认可的是全部三种布局形态。nested 与 legacy flat 同时存在时（迁移后最常见
的现状），entry 直接落在 legacy flat 根上的 artifact 不被认作"坐在容器根上"，`?path=`
就能把该 agent 在 legacy 根下的 `credentials.json`、`skills/*/.env` 服务出去——而这条路由只凭
view token、不过 JWT。现在直接复用 [[confinement]] 的 `owner_roots`（三种 workspace 形态 + team
目录）。两边都是字面串比较：`confine_entry` 已证明 realpath 后的 entry 落在其中某个字面根内，
**不要**再对 `artifact_root` 做 realpath（会和刚校验过的口径分叉）。
测试：`test_raw_refuses_siblings_of_an_entry_at_the_legacy_flat_root`（同时钉住 basename 别名仍可用）。

## 2026-10-28 — 限定改为"属主根"而不是 base

原来只检查 `artifact_root` 在 `base_working_path` 之下——那是所有租户的总根，一条指向
别的用户目录的 `file_path` 照样通过。改为 [[confinement]] 的 `confine_entry`：entry 必须
在该 agent 的 workspace 候选目录（当前 nested + legacy flat）内，团队 artifact 另加该 team
的共享目录；**用户整根不在其中**（兄弟 agent 的文件同样 404）。后续的"单文件模式"和子路径
限定不变；单文件模式判定用的容器根（workspace、team 目录）也改为字面路径（base 已
realpath）——`confine_entry` 已拒绝链接根，这里再 realpath 只会和刚检查过的根口径不一致。

## 2026-08-24 (#349 M3) — `application/x-url` 也进渲染标记集

URL tab 的 entry 是 `tabs/<slug>/page.url.json`——`application/x-url` 同样
是内部渲染标记不是真 MIME。加入 `_RENDER_MARKER_KINDS` 后,raw 路由对它的
**对外 Content-Type 从 `application/x-url` 变为 `application/json`**
(经 guess_type 内建表,.json 不依赖平台 mime.types)。CSP 分支读的是
`resolved.kind`(保持 `application/x-url` 不变),测试同时钉住两者。
「哪些 kind 是渲染标记」现与前端 kindRegistry 的非静态-downloadExt 集合
一致——r1 两边曾漂移一轮,同步机制目前是注释互指。

## 2026-08-21 — 渲染标记 kind 不再当传输 MIME(深圳复测 .bin bug 后端面)

entry 的 `media_type` 原来无条件 = `art.kind`,但
`application/vnd.officecli-live` 是内部渲染标记不是 MIME——直开 raw URL
时浏览器只能按未知二进制处理。`_RENDER_MARKER_KINDS` 里的 kind 改按
entry 扩展名给真实类型;office 三种扩展名用**显式映射**
`_OFFICE_MIME_BY_EXT` 而非 `mimetypes.guess_type`——stdlib 对
pptx/docx/xlsx 的认知来自平台 mime.types 文件,开发机上对、slim 容器里
就是 octet-stream(环境轴假绿)。前端下载文件名是该 bug 的决定性一层,
见 [[../../../../frontend/src/components/artifacts/kindRegistry.ts]]。

## 2026-08-10 (方案 B 的后果修正) — 团队根纳入单文件守卫

单文件守卫原本只认 workspace 根：entry 直接坐在根上时拒绝子路径请求，**正是为了防止一个 token
顺着走该 agent 的其他文件**。团队 artifact 被要求住进团队目录后，entry 出现在一个该守卫不认识
的根上——于是一个团队 artifact 的 view-token 会把**团队目录里的任何文件**都提供出去，而这条路由
是**刻意绕过 JWT** 的（token 本身就是凭据）。

现在 `container_roots = {workspace_root} ∪ {team_shared_dir}`（team 从 artifact 行上取）。

# raw_access.py — resolve an artifact + sub-path to the file it serves

## Why it exists

Extracted 2026-07-21 from `backend/routes/artifacts/public.py::get_raw`,
which had grown into a fat handler mixing HTTP concerns with pointer/path
logic. The split: this module owns everything that is NOT HTTP (pointer
lookup, flat→nested workspace fallback, path-escape confinement, the
workspace-root single-file rule, media-type choice); the route keeps token
verification and response headers (CSP). Covered by
`tests/artifact/test_raw_access.py`.

## Rules (all realpath-based so symlinks cannot escape)

- Artifact root (dirname of entry) must stay inside
  `settings.base_working_path`.
- **Workspace-root single-file mode**: when the entry sits directly at the
  agent workspace root, sub-path requests are refused (the sibling tree would
  be the whole workspace — Bootstrap.md and every other artifact's files).
  The entry's own basename is tolerated as an alias of the entry.
- Sub-paths are realpath-confined to the artifact root.
- Media type: entry serves as the artifact's `kind` — EXCEPT render-marker
  kinds (`_RENDER_MARKER_KINDS`: officecli-live, x-url), whose transport type
  derives from the entry's extension (2026-08-21/24 entries above); assets are
  guessed via `mimetypes` (the kind describes the entry, not a sibling
  style.css).

## Error contract (the frontend depends on it)

- `ArtifactNotFound` (404): artifact missing, token/agent mismatch, path
  outside the root. Uniform 404 so probes can't map what exists.
- `ArtifactContentGone` (410): row exists but content is gone (empty
  file_path on legacy rows, entry/asset off-disk). 410 is the self-heal
  trigger in every renderer — never merge it into 404.

## Gotchas

- Escape attempts are logged (`path-escape blocked: ...`) before raising —
  keep the log, it is the only audit trail for probe attempts.
- `ResolvedRawFile.is_entry` drives the route's CSP choice (entry HTML gets
  the host-source CSP; assets get a generic strict one).
