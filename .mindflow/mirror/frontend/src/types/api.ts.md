---
code_file: frontend/src/types/api.ts
last_verified: 2026-05-27
stub: true
---

## 2026-05-27 — LarkErrorDetail (translator output)

`LarkBindResponse` now optionally carries `error_detail: LarkErrorDetail`
on failure. Field names match the backend `_lark_error_translator`
`ErrorTranslation` dataclass 1:1 so the JSON round-trip works without
adapters. `LarkConfig.tsx` renders this as a structured card with
title/message/action_hint/console_url; falls back to plain `error`
when absent.

## 2026-05-14 — FileInfo becomes a recursive tree node

Spec: `reference/self_notebook/specs/2026-05-14-artifact-pointer-model-design.md`

Mirrors the backend `api_schema.FileInfo` change. The flat
`{filename, size, modified_at}` shape became a recursive tree node:
`{name, path, is_dir, size, modified_at, children?: FileInfo[] | null}`.
`FileListResponse.files` renamed to `tree`. `FileDeleteResponse.filename`
renamed to `path` because the backend DELETE accepts nested relative paths.

## 2026-05-13 — Phase C: ActiveRunInfo + AgentInfo.active_run

Adds the frontend mirror of the backend ActiveRunInfo type so the
GET /api/auth/agents response carries enough metadata to render the
"Running" indicator across tab reloads / devices. Field set matches
`xyz_agent_context.schema.api_schema.ActiveRunInfo` exactly.

# types/api.ts

## 为什么存在

前端与后端通信的全部 TypeScript 类型定义，对应后端的 Pydantic 响应模型（`src/xyz_agent_context/schema/api_schema.py`）。任何 API route 返回的数据形状在这里都要有对应 interface。

## 2026-04-21 · v2 时区协议

`Job` 和 `DashboardPendingJob` 接口里的 UTC 字段全部替换为 β：

```ts
// removed:
// next_run_time?: string;
// last_run_time?: string;

// added:
next_run_at?: string;
next_run_timezone?: string;
last_run_at?: string;
last_run_timezone?: string;
```

背景见 `reference/self_notebook/specs/2026-04-21-job-timezone-redesign-design.md`。前端不再感知 UTC——所有时间都以 "local + tz" 配对流动。

## 新人易踩坑

- 不要"为了方便前端排序"悄悄加回 `next_run_time: string`。β 之间不可比较（跨时区 job 无全序），排序/筛选的"时间 cursor"只存在于后端 α 里
- 如果后端 response 新增时间字段，**必须**同步配 `_timezone` 字段，不能只有时间主体
