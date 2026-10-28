---
code_file: frontend/src/components/jobs/JobsPanel.tsx
last_verified: 2026-09-11
---

## 2026-09-10 — `canResume` + `paused_spend_cap`

与后端 `job_recovery._RESUMABLE_STATUSES` 对齐：被日花费上限暂停的 job 面板上有 Resume 按钮
（早恢复无害，下一次调度会重判 cap）。

## 2026-09-09 — wired the payload/title/description edit dialog (GitHub #86)

`editingJob`/`handleEditSchedule`/`JobScheduleEditDialog` already
covered the job's execution time; added the identical shape for content
— `editingPayloadJob` / `savingPayload` state,
`handleEditPayload`/`handleSavePayload` handlers calling the new
`api.updateJob`, and `<JobPayloadEditDialog>` rendered alongside
`<JobScheduleEditDialog>`. Reuses the existing `canEdit(status)`
predicate (passed once as `canEdit`, which gates both buttons) rather
than inventing a second one — editing either the schedule or the
content of a running/terminal job is equally meaningless, so the same
non-running, non-terminal guard applies to both.

`handleSavePayload` sends the job's OWN `agent_id` (not the panel's
focused `agentId`; the backend's ownership check keys on the job's
owner), closes the dialog and refreshes only on `success`, and on a
`{success:false}` result (its `message`) or a thrown `ApiError` keeps
the dialog open behind a `useConfirm` alert so the user can retry.
Test: `__tests__/JobsPanel.editPayload.test.tsx`.

## 2026-08-27 — 密度重构：band 按数据条件渲染

Owner 反馈「jobs 页面字太多」。测下来根因不是字号，是**常驻元素数量**：
面板在第一个 job 之前无条件铺六条 band——只放一个刷新图标的 toolbar、
四格 StatStrip、独立标题行的分布条、三个文字视图 tab、11 枚状态筛选片，
400px 抽屉下合计约 354px chrome，其中约 289px 对典型 agent 是零信息量
（四个 0、一条单色分布、10 枚点下去必然空列表的筛选片）。字号砍 20%
只能省约 70px，还会掉出 design_system §4 的字号阶梯。

新的密度契约：**一条 band 只在它承载的数据非空时渲染**。所有「哪条 band
存在」的规则移到 [[jobsPanelModel]]，因为条件渲染正是最容易静默回归的
东西，必须可测。

五处改动：

- **Header** — embedded 模式不再有自己的 header（抽屉外壳已经写了标题），
  孤零零占一整条 band 的刷新按钮并入下面的控制行。非 embedded 的
  `CardHeader` 本来就是标题+刷新一行，不变。
- **Metrics** — `StatStrip` 调用与 `StatusDistributionBar` 一起换成
  [[JobStatusMeter.tsx]]（一条 3px 条 + 只列非零状态的行内图例）。
  `StatusDistributionBar.tsx` 已删除。
- **Filters** — 筛选片由 `filterOptions(allJobs)` 派生：只渲染有 job 的
  状态，每片带计数，恒定含 `all`；0 job 时整行不渲染。视图切换从三个
  文字 tab 换成图标 segmented，右对齐并入同一行（tooltip 走 §6 决策表的
  `ui/tooltip.tsx`，不是 title 属性）。
- **Row** — 卡片换成 [[JobRow.tsx]]，描述移入 [[JobExpandedDetail.tsx]]。
- **Empty** — 空态换成 `nm/BracketEmptyState`（§6 决策表），不再手搓
  56px 圆角图标底板（§5 规定 h-5~h-8 只用于空态插图）。

结果：chrome 354px → 40px，列表可视区从约 26% 提到约 90%。

**筛选片自愈**：片是从数据派生的，所以最后一个 failed job 被解决时
`Failed` 片会消失。此时 `activeFilter` 派生回 `'all'`（不是用 effect
改 state，避免闪一帧空列表），否则用户会被困在一个空列表上、且没有可见
的筛选片可以清除。

**控制行不需要 sticky**：它是 `CardContent` 的兄弟节点，在滚动视口
**外面**，200 个 job 也一直可见。

## 2026-07-30 — status filter chips wrap instead of scrolling invisibly

> 2026-08-27 更新：这条的根因已在上面被从源头解决——片少了自然放得下。
> 换行（`flex-wrap`）仍然保留并仍有测试守着，作为安全网。

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
- **Downstream:** `JobRow` (+ `JobExpandedDetail` as its child),
  `JobStatusMeter`, `JobDependencyGraph`, `JobExecutionTimeline`,
  `JobDetailPanel`, `lib/jobsPanelModel`
- **Consumed by:** right-panel tab layout, `BookmarkPanelHost` (embedded)

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
  `running` means the job script is executing. They are separate chips (the
  old merged KPI tile is gone); [[jobStatusVisuals]] gives `active` ink and
  `running` a warning tint, so the noteworthy one is the one that stands out.
- `refreshJobs` must receive `(agentId, userId)` — calling it without
  arguments silently does nothing (preloadStore signature).
