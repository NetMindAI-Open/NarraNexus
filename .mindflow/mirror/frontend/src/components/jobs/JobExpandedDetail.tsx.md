---
code_file: frontend/src/components/jobs/JobExpandedDetail.tsx
last_verified: 2026-07-30
---

## 2026-07-30 — 「编辑时间」按钮 + cron 显示死代码修复

Actions 区新增「编辑时间」按钮（`canEdit` / `onEdit` props；非运行非终态可见），
点击把 job 上抛给 `JobsPanel` 打开 `JobScheduleEditDialog`。同时修了 configuration
区的 cron 显示：原来读 `trigger_config.cron_expression` 和 `trigger_type`，而后端
`GET /api/jobs` 透传的是 `cron`（无 `trigger_type`），所以那两段一直是死代码——
改为读 `trigger_config.cron`，删掉 `trigger_type` 分支。

# JobExpandedDetail.tsx — Full field inspector for a list-view job row

Extracted from the inline section that used to live inside `JobsPanel`. Shows
all metadata sections: IDs, configuration (type, trigger, cron), payload,
timing, dependencies, process log, target user context, linked narrative, last
error, and cancel button.

## Why it exists

The inline expand logic was growing `JobsPanel` significantly. Extracting it
here makes `JobsPanel`'s render function readable and allows `JobExpandedDetail`
to have its own `payloadExpanded` state without polluting the parent.

## Upstream / downstream

- **Upstream:** raw API `Job` type (not `JobNode` — needs all fields)
- **Downstream:** `onCancel(e, jobId)` callback back to `JobsPanel`
- **Used by:** only `JobsPanel` list view

## Design decisions

**CopyableId sub-component:** Clickable ID pills with a 1.5 s checkmark
animation. Uses `stopPropagation` to prevent the copy click from toggling the
parent row's expand state.

**Payload truncation:** Long payloads are capped at 3 lines in preview mode.
The "show more / show less" toggle is local state, so it resets when the row
is collapsed and re-expanded.

**`onClick` stop propagation on the container:** The entire detail div calls
`e.stopPropagation()` so clicks inside the expand area don't collapse the row.

## Gotchas

- `triggerConfig` is typed as `Record<string, unknown>` on the API type.
  Fields like `cron_expression`, `interval_seconds`, etc. are cast with `as`
  inside the component. If the backend changes field names, these casts will
  silently produce `undefined` without a type error.
