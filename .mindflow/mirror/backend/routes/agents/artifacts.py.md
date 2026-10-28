---
code_file: backend/routes/agents/artifacts.py
last_verified: 2026-10-28
stub: false
---

## 2026-10-28 — office-asset 落盘目录先过属主限定

`entry_dir` 原来直接从 `base + art.file_path` 推出，指针越界就能把资产写进别人的目录。
现在先经 `ArtifactService.resolve_owned_entry`（内部是 [[confinement]]；越界 404），再取 dirname。

## 2026-07-22 — URL-tab endpoints

The `POST .../artifacts/url` handler computes the browser origin via
`artifacts_public._app_origin(request)` and passes it to
`ArtifactService.open_url` as `app_origin`, so the self-origin guard is robust
even if `public_base_url` is misconfigured (defense in depth for the
allow-same-origin URL iframe).

Added `POST /{aid}/artifacts/url` (open a web page as a URL tab; SSRF-gated,
probes embeddability) and `POST /{aid}/artifacts/{id}/embed-mode` (set/clear
the user's manual embed override). Both are thin shells over
`ArtifactService.open_url` / `.set_embed_mode` — same auth + error-mapping
pattern as the rest of this router.
## 2026-07-21 — thinned to an HTTP shell; heal moved to ArtifactService

Artifact business logic left this file for the new
`narranexus/platform/artifact/` package ([[artifact_service.py]]):

- The whole heal recovery strategy (`_KIND_EXTENSIONS`, workspace scan,
  three-step sequence) moved to the service ([[heal.py]]); the endpoint now
  just maps auth + `ArtifactError.code` → HTTPException. `HealResponse` /
  `HealCandidate` became the shared schema models `HealResult` /
  `HealCandidate` (same field names — the wire shape is unchanged).
- Register delegates to `ArtifactService.register` (the same single
  implementation the MCP tool and bootstrap use) instead of importing the
  module-private `_common_tools_impl.artifact_runner`.
- PATCH title now goes through `ArtifactRepository.update_title` instead of a
  raw `db.update("instance_artifacts", ...)` inline in the handler.
- The repeated get+ownership-check pattern collapsed into
  `_get_owned_artifact` (still 404 on mismatch — no existence leak).

References to `artifact_runner` in older entries below are historical; the
code lives in `narranexus/platform/artifact/_artifact_impl/registration.py` now.

## 2026-05-20 — stale "quota" wording removed

The per-user artifact quota was removed in v1.7.0 (see [[artifact_runner.py]]).
Two leftover mentions of a "validation/quota path" / "same quota" in this doc
were factually wrong (no quota is enforced anywhere) and have been corrected to
just "validation" / path-confinement.

## 2026-05-19 — new POST /{aid}/heal endpoint

Self-heal for artifacts whose pointer is broken (file_path NULL or off-disk
— legacy rows, killed-mid-register processes, agent file moves). Front-end
renderers call this on 410:

1. If the existing pointer is fine on disk → return recovered=True (handles
   transient 410 races).
2. If the caller passed `entry_path` → re-register onto that path
   (target_artifact_id = the artifact). This is the "user picked from the
   modal" path.
3. Otherwise scan the agent workspace for files whose extension matches the
   artifact's kind (`_KIND_EXTENSIONS` table). Sort by mtime desc, cap at
   `_HEAL_MAX_CANDIDATES`. Single match → auto-register. 0 or >1 →
   return `candidates` so the renderer can render `<ArtifactHealModal>` for
   the user to pick.

All registrations route through `artifact_runner.register_artifact` with
`target_artifact_id` set, so path validation / kind whitelist /
MAX_ARTIFACT_BYTES still apply uniformly.

## 2026-05-14-r3 — `delete_source` removed; deletion is registry-only

DELETE no longer accepts `?delete_source=`. The agent's workspace files are
NEVER touched by this endpoint. Reason: the rmtree branch forced a brittle
"entry can't sit at workspace root" register-time rule (else delete-source
wipes the whole workspace) AND introduced a shared-directory footgun (two
artifacts in one dir → one delete wipes both). Both classes of bug
disappear when deletion is registry-only. The user cleans workspace files
via the workspace section in the config panel — same place they already go
to inspect / download / preview files.

Response shape: `{deleted: artifact_id}` (no more `source_deleted`).

## 2026-05-14 — pointer model: raw serving moved to public router

This router shed its `/raw` endpoint. Raw content serving now lives in
`artifacts/public.py` under `/api/public/artifacts/raw/{token}/{path}`
(JWT-bypassed; HMAC token in the path is the auth). The split is forced by
the multi-file HTML use case: an iframe `src=` must point at a real URL (a
`blob:` URL breaks relative sub-resource resolution) but native iframe loads
cannot attach Authorization headers — so the raw content must be on a
JWT-exempt path. The token mechanism is in `artifacts/_token.py`.

New / changed endpoints in this file:
- POST `/{agent_id}/artifacts/register` — manual register (the workspace tree
  viewer "register as artifact" action). Delegates to
  `artifact_runner.register_artifact` so MCP and UI go through the same
  validation path.
- GET `/{agent_id}/artifacts/{aid}/view-token` — mints a short-TTL HMAC token
  and returns `{token, raw_url, expires_at}`. Frontend calls this once per
  artifact view; `raw_url` is the directory-style URL the iframe `src=` (or
  blob-fetcher) should hit.
- DELETE `/{agent_id}/artifacts/{aid}?delete_source=bool` — `false` (default)
  removes only the DB row; `true` also `rmtree`s the artifact root folder
  (defence-in-depth: confined to the agent workspace AND not equal to it).
- GET detail no longer returns `versions` — under the pointer model there
  is no version list.

`_verify_agent_ownership` is unchanged. The `_resolve_agent_user_id` helper
was added so the manual-register endpoint can find the workspace owner
(matches the `agent_runtime` rule that overrides `ctx.user_id` with
`agent.created_by`).

# agents/artifacts.py

## Why it exists

HTTP boundary for the artifact lifecycle: list, manually register a workspace
file, fetch metadata, mint a view token for raw access, edit pin/title,
delete (optionally with source files). Artifacts are pointers to entry files
the agent wrote in its own workspace; this router is the JWT-authed
control plane on top of them.

Kept separate from `artifacts/public.py` (raw content, token-authed,
JWT-bypassed) — the auth model differs, so the split is structural.

## Upstream / Downstream

Upstream:
- Frontend `artifactStore` / `ArtifactColumn` calls `GET .../artifacts` to
  populate the tab list per session / pinned set.
- Frontend renderers call `GET .../view-token` once per artifact, then load
  raw bytes from the returned `raw_url` (under `/api/public/artifacts/...`).
- Frontend `ArtifactsSection` / `FileUpload` workspace UI calls `PATCH` /
  `DELETE` / `POST .../register`.

Downstream:
- `ArtifactService` (narranexus/platform/artifact) for register + heal.
- `ArtifactRepository` for plain CRUD (list / get / pin / title / delete).
- `_artifact_token.mint` for view-token minting.

Mounted under `/api/agents` (see `backend/main.py`).

## Design decisions

**Raw content lives on a separate, JWT-exempt router** (`artifacts/public.py`).
Reasoning above — iframe `src=` for multi-file HTML cannot carry an
Authorization header. The HMAC token in the URL path is the auth.

**Manual register and the MCP tool share `ArtifactService.register`.**
Same path-confinement, same kind whitelist, same size cap. (The old "must be
in a subdirectory" hard rule is gone — workspace-root entries are legal
single-file artifacts since 2026-05-14-r3.)

**Deletion is registry-only** (2026-05-14-r3): no `delete_source` parameter
exists anymore; workspace files are never touched by DELETE.

**`agent_id` ownership check on every endpoint**, via `_get_owned_artifact`.
Returns 404 (not 403) for mismatches to avoid leaking existence information.

## Gotchas

- The `view-token` payload decoding (to surface `expires_at`) inlines base64
  url-decode rather than going through `_artifact_token.verify` — `verify`
  would treat its own freshly-minted token as valid, but we want to return
  the `exp` without paying for a second HMAC pass.
- `register_artifact` (manual) is always agent-scoped (`session_id=None`).
  There is no UI flow today to attach a manual registration to a chat
  session; if that's wanted later the route can accept `session_id`.
- The router is mounted at `/api/agents`. The detail / view-token /
  patch / delete routes share the `{artifact_id}` segment; FastAPI does
  not match nested literal paths (`view-token`) against `{artifact_id}`
  because the segment count differs, so ordering between them doesn't
  matter — but `POST .../register` is declared before the bare detail
  GET for clarity.
- Authentication is handled by FastAPI middleware (`backend/auth.py`), not
  here. The handlers only consume `request.state.user_id` for ownership.

## 2026-07-13 — office-live kind 的扩展名映射

`_KIND_EXTENSIONS` 新增 `application/vnd.officecli-live` → (.pptx,.docx,.xlsx),让 heal(按扩展名找回断掉的指针)对 office artifact 也生效。

## 2026-08-18 — scope=context

list_for_agent_context 的 HTTP 暴露:前端全量拉(打开/切 agent/WS 重连)用,
与 agent 状态块同一可见面。session/pinned 语义不变。

## 2026-08-19 — PUT /{aid}/content(用户编辑保存)

session 鉴权(view-token 永远只读);409 的 detail 是结构化
`{error, current_hash}` —— 编辑器要拿它 re-base,别改成纯文本。
其余 ArtifactError 照 e.code 直映。

## 2026-08-19(二)— POST /{aid}/office-edit-commit

watch 页用户编辑的登记提交(hash/history user_edited/事件),幂等。

## 2026-08-19(三)— POST /{aid}/office-asset

T2 图片替换的落盘半步:multipart 存 entry 同目录,文件名=随机前缀+
净化 basename(带路径分隔/点串的敌意名进不来目录外),10MB 封顶,
返回绝对路径供 `set src=` 用——watch server 与 entry 同一文件系统
(本地同机;云端 executor 路径一致性在手动清单)。

## 2026-08-20 — 上传/PUT 体积门 + 文件名 Unicode 净化(#334 I3/I15)

office-asset:声明长度快拒 + 1MB 分块流式写(越界 413 且**不留半个
文件**——entry 目录会被 raw 路由端出去);PUT /content:路由级依赖按
Content-Length 快拒(service 的 MAX_ARTIFACT_BYTES 仍是第二道,防
伪造 header)。文件名净化改 `\w`(UNICODE)——全语种字母数字放行,
分隔符/控制符打下划线,截断(防 ENAMETOOLONG 变 500;r3 起 stem/ext 分截,见下节);
原 CJK-only 白名单是「只想到中文」的物证,已除。

## 2026-08-20(二)— 体积门真相版 + 截断保后缀(#334 r3 C1/I1)

**r3 更正**:FastAPI 在解依赖**之前**就读完 body,路由依赖做不成
「快拒」。真门=`backend/middleware/body_size.py`(HTTP 中间件,按
方法+路径前缀查 Content-Length,唯一先于框架缓冲的层)。
PUT /content **无 Pydantic body 形参**(有则框架预缓冲复活)——
`request.stream()` 累计 25MB+2MB 上限后自行 model_validate_json,
409 结构化 detail 形状不变;**给它加回 body field = 重开无界缓冲洞**。
office-asset 保留 UploadFile:框架已 spool 到磁盘,函数内检查约束的
是「拷出 spool 的量与落盘物」,不是内存门——注释已改实话。
文件名截断改 stem[:104]+ext[:16] **分开截**(一刀切 [:120] 会吃掉
后缀;raw 路由按扩展名判 media_type,症状=「替换成功但没图」)。

## 2026-08-20(三)— office-asset 路由侧声明长度快拒删除(#334 r5 M4)

上两节提到的 office-asset「声明长度快拒」已删:Content-Length 覆盖
**整个 multipart body**(boundary/part header 在内),拿它比单文件
10MB 会把 9.99MB 的合法图片 413 掉;声明门归中间件(带
MULTIPART_FRAMING_MARGIN),文件字节的真实 10MB 由流式拷贝那道执行
(越界 413 且不留半个文件,那才是比文件字节的门)。413 文案改由
常量算出,改上限不再产生说错数字的错误消息。
