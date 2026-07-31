---
code_file: frontend/src/components/jobs/JobsPanel.tsx
last_verified: 2026-07-30
---

## 2026-07-30 — status filter chips wrap instead of scrolling invisibly

Owner report: "任务列表里筛选 全部/进行中/已暂停 没显示全，只显示了 6-7 个".

The 11 chips were one `whitespace-nowrap` row inside
`<ScrollArea horizontal hideScrollbar>`. In zh they measure ~660px; this panel
normally lives in a 300–440px bookmark drawer, so the row clipped after ~7
chips — with the scrollbar suppressed by `hideScrollbar` and no edge fade,
there was nothing on screen suggesting the other four existed. (Trackpad users
could scroll it by accident; mouse users could not reach them at all.)

Now a plain `flex flex-wrap` row: two or three lines, a few px of height, and
nothing hidden. Guarded by `jobs/__tests__/jobsFilterRow.test.tsx`, which
asserts both that every chip renders and that the row is no longer inside a
scroll viewport.

## 2026-07-30 — reschedule (编辑执行时间) 接线

新增 `canEdit(status)`(= 非 running/completed/cancelled/failed)、`editingJob` /
`savingSchedule` 状态、`handleEditSchedule`(打开弹窗)、`handleSaveSchedule`(调
`api.updateJobSchedule`,成功 refreshJobs,失败走 `alert()` 显示 ApiError.message)。
渲染 `JobScheduleEditDialog`(放在 `inner` fragment 顶部,embedded/非 embedded 都生效)。
canEdit/onEdit 透传给 `JobExpandedDetail`。

# JobsPanel.tsx — Root orchestrator for the Jobs panel

The single place where view-mode switching, status filtering, inline expand,
and cancel are coordinated. It is intentionally large because those concerns
interact: cancelling a running job must also close the expand row and refresh
the list.

## 2026-06-10 — embedded mode + onJobResolved callback

Two additive props for the bookmark-drawer ActivityPanel ([[ActivityPanel]]):
`embedded` drops the outer Card + duplicate title (host section already
names the panel; functional actions like Refresh stay), and
`onJobResolved(jobId)` fires after a successful cancel/resume so the
bookmark layer can clear a failed job's 'attention' state. Default
rendering is byte-identical for existing call sites.

## Why it exists

Without this top-level orchestrator, each sub-component would need to share
mutable state (selected job ID, cancelling flag, filter) through props or a
separate store. Keeping it here avoids over-engineering a store for a panel
that is self-contained.

## Upstream / downstream

- **Upstream:** `usePreloadStore` (jobs data, refreshJobs), `useConfigStore`
  (agentId / userId), `api.cancelJob()`
- **Downstream:** `JobExpandedDetail`, `JobDependencyGraph`,
  `JobExecutionTimeline`, `JobDetailPanel`, `StatusDistributionBar`, `KPICard`
- **Consumed by:** right-panel tab layout

## Design decisions

**`jobToJobNode` conversion:** Transforms the API `Job` type into `JobNode`
needed by graph/timeline. Prefers `instance_id` over `job_id` as the node ID
because dependency references use instance IDs.

**Failed-job collapsing:** Separates failed jobs into a collapsible group at
bottom when filter is "all", so active/pending jobs stay visible by default.

**Cancel flow:** Calls native `confirm()` before `api.cancelJob()`. Deliberate
friction because cancels are irreversible. Cancel state is tracked per-job-id
so the loading spinner appears on the correct row.

## Gotchas

- The status filter `'active'` and `'running'` are both "in progress" but are
  different backend states — `active` means the Module instance is alive,
  `running` means the job script is executing. The KPI metric merges both.
- `refreshJobs` must receive `(agentId, userId)` — calling it without
  arguments silently does nothing (preloadStore signature).
