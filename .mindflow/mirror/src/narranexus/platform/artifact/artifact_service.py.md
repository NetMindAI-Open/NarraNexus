---
code_file: src/narranexus/platform/artifact/artifact_service.py
last_verified: 2026-10-28
stub: false
---

## 2026-10-28 — `resolve_owned_entry(artifact)`

把 [[confinement]] 暴露成服务方法，供 backend 路由（office-asset 上传）取"已证明在属主根内"
的 entry 绝对路径；路由不再 import `_artifact_impl`。

## 2026-08-07 — `register()` 增加 team_id / event_id 两个语义参数

两者都**不是模型可填的**，均来自服务端身份 header（见 [[_mcp_identity.py]]）：

- `team_id` — 本回合所属 team（None = 私有）。工具层把「模型只能收窄」的规则解析完再传进来，
  service 只负责透传，不做 scope 决策。
- `event_id` — 本回合的 events 行 id，落进归因日志回答「哪一次 turn 改的」。

service 仍是薄桥接：scope 语义在 [[artifact_tool.py]]，路径与写库在
[[registration.py]]。

## 2026-07-22 — URL-tab domain operations

`open_url` gained an `app_origin` param: the HTTP route derives the
browser-visible origin from the request and passes it so the self-origin
guard holds even when settings.public_base_url is unset (the MCP path leaves
it None). See [[url_artifact.py]] for the guard.

Added `open_url()`, `set_embed_mode()` for the URL-tab feature (see
[[url_artifact.py]] / [[embed_probe.py]] / [[url_safety.py]]). They follow the
same thin-bridge shape as register/heal/resolve_raw_file — the service stays a
domain-operation surface, not a CRUD facade.
# artifact_service.py — public protocol layer (Service + Bridge)

## Why it exists

Single entry point for artifact **domain operations**: `register` (pointer
registration, ex-`artifact_runner`), `heal` (broken-pointer recovery,
extracted from the agents_artifacts route handler), and `resolve_raw_file`
(raw-content path resolution, extracted from the artifacts_public route
handler). Concrete logic lives in `_artifact_impl/`; this class is the bridge,
mirroring the NarrativeService / ModuleService pattern.

## Deliberate scope boundary

Plain CRUD (list / get / delete / set_pinned / update_title) intentionally
stays on `ArtifactRepository` — the service is NOT a pass-through facade over
every repository method. Rule of thumb: if the operation has rules beyond a
single table write, it belongs here; otherwise call the repository.

## Upstream / Downstream

- Constructed per-request with an `AsyncDatabaseClient` (stateless besides the
  repo handle — cheap, matches how routes and the MCP tool get their client).
- Called by: `artifact_tool.py` (MCP), `agents/artifacts.py` (manual register
  + heal), `artifacts/public.py` (raw serving), `bootstrap/profiles.py`
  (welcome artifact).
- All failures raise the `ArtifactError` hierarchy (`.code` → HTTP status), so
  MCP and HTTP callers convert uniformly with a single except clause.

## 2026-08-18 — `bulk_delete` 上移进 Service(事件化的连带)

文件头说「plain CRUD 留在 Repository」——bulk_delete 是**刻意的例外**:事件化让删除
变成域操作(归属校验+行捕获+删除+staging "deleted"),调用方不允许「只拿删除不拿
事件」,所以路由薄化、逻辑收进来。register 的三个分支(新建/重注册/去重原地)也在
_impl 里各自 stage(registered/updated)。

## 2026-08-19 — save_user_content(用户编辑提交管线)

Spec A §3 的唯一用户编辑提交口,薄桥接到 [[user_edit.py]]。Spec B 的
office user-edit-commit 将复用同一落点语义(hash/history/事件)。

## 2026-08-19(二)— refresh_external_state / commit_office_user_edit

5① 侦测与 5② office 提交的薄桥,见 [[freshness.py]] 与 [[user_edit.py]]。

## 2026-08-20 — service 自持 `_db`,impl 显式收 db(#334 I9)
