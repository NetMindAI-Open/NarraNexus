---
code_file: frontend/src/components/jobs/JobExpandedDetail.tsx
last_verified: 2026-09-11
---

## 2026-09-09 — Edit Content button next to Edit Time (GitHub #86)

Payload was already displayed here (expand/collapse), but it was
read-only — the backend's PUT /api/jobs/{job_id} had no frontend edit
UI at all. Added an `onEditPayload` prop next to `onEdit`
(execution-time editing). Both buttons sit behind the SAME `canEdit`
prop (the caller's `canEdit(status)`: non-running, non-terminal) — one
gate, not a second boolean that always carried the same value — same
action-row placement, and the new one opens `JobPayloadEditDialog`
instead of `JobScheduleEditDialog`.

## 2026-08-27 — description 从折叠行搬到这里（区块 0）

密度重构（见 [[JobsPanel.tsx]]）把 `job.description` 从列表行移了进来，
渲染在所有区块之前。在行里它被 `line-clamp-1` 截成碎片
（"Once a day, drop by with a fresh topic. Pause or…"），在抽屉宽度下
承载的信息量是零；在这里它有空间做一个完整句子。行的第二行改放
真正会被读的东西——调度规则 + 下次/上次执行，由
[[jobsPanelModel]]`.describeRow` 产出。

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

## 2026-08-19 — 显示 end_at 调度地平线

configuration 区在 interval 之后渲染 `jobs.expanded.endAt`（"Runs until:"，
取日期部分）：没有它，有界例程（"每天一次跑两周"）与无限期 job 在 UI 上
无法区分，用户看不出它会自己停。**对所有带 end_at 的 job 渲染是对的**：
平台对 scheduled 与 ongoing **两条 finalize 路径**都强制该地平线（四轮
review B 选了"真通用"路线，含 ONGOING 的 hook 接管 else 支）。one_off 的
MCP schema/instructions 不教 end_at、trigger 也忽略，所以它本不该携带；
但没有硬校验拒绝，万一带了这里只多显示一行无害的 "Runs until:"。
类型来自 `types/api.ts` 的 `TriggerConfig.end_at?`（与后端 schema 对齐）；
i18n `jobs.expanded.endAt` 十个 locale 全量（该块历史上就是全量翻译的，
与 onboarding.* 只有 en/zh 的口径不同）。编辑对话框有意不加该字段
（reschedule_job 的 _RESCHEDULE_FIELDS——2026-09-10 前叫 _TIME_FIELDS——不含它，传了会被静默忽略）。
