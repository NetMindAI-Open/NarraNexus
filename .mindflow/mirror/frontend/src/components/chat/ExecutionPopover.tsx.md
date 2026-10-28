---
code_file: frontend/src/components/chat/ExecutionPopover.tsx
last_verified: 2026-08-31
stub: false
---

## 2026-08-31 — 对照面改名（无行为变化）

相位标签「和谁保持一致」的注释里，`ProcessPanel` 换成
[[process/RunPhases]]——前者已随过程框拆除退役。**一致性规则本身没变**：
顶层相位取 `PHASE_LABEL_KEYS` 的本地化名，真正的子步（`3.4.x`、fallback）
保留后端 title，那正是本 popover 要暴露的细节。

## 2026-08-26 — phase 标签走 PHASE_LABEL_KEYS，两面不再打架

此前 chip 和步骤列表直接渲染后端原始 `title`（带 emoji 的英文
"Execute Agent Loop" 等），而当时的 `ProcessPanel`（今 [[process/RunPhases]]）对同一 step 显示本地化
label —— 同一时刻两个 surface 文案不一致。现在通过 `stepLabel()` 走
[[processShared]] 的 `PHASE_LABEL_KEYS`：顶层相位显示与 ProcessPanel
相同的本地化名；真正的子步（工具 `3.4.x`、fallback）没有映射，保留自己
的后端 title —— 那正是这个"详细窥视"面板要暴露的细节。`currentStage`
芯片同样改走 `stepLabel`。

## 2026-07-03 — current-stage chip (not a fake fraction) + surfaced detail

The chip showed `· {completed}/{steps.length}`, but steps.length is only
"steps seen so far", never a real total — the pipeline streams an unknown
number of steps (the agent loop tool-call count is decided by the LLM at
runtime), so it always read as X/(X+1) and meant nothing. It now shows the
CURRENT stage by name (latest running step, else the last step). The popover
list also surfaces each step's `description` and `details.selection_reason`
(e.g. the narrative match summary + why it was chosen) — that data already
flowed into Step.description/.details but was being dropped; it wraps rather
than truncates so the reason is readable.

# ExecutionPopover.tsx — Clickable Processing chip with live steps

## 为什么存在

RuntimePanel's execution view was retired in the bookmark redesign on
the grounds that TurnTimeline covers it — Owner review found the
pipeline-step view still wanted ("点击 processing 标记出来弹窗显示
execution 步骤"). The chip in the chat header is now the trigger; a
Radix popover lists the run's steps live.

## 上下游关系

- **被谁用**: ChatPanel header (rendered only while isStreaming).
- **依赖谁**: chatStore.currentSteps via ChatPanel (passed as a prop —
  keeps this component pure/presentational and trivially testable).

## 设计决策

- Steps render in arrival order; `step` containing a dot = substep
  (indented). Status icons: completed ✓ / running spinner / failed ✗ /
  pending ○. Chip shows completed/total count.
- No StatStrip/progress-bar resurrection — that was developer
  telemetry; the step list is the user value.

## 新人易踩的坑

Visible only during streaming: the chip IS the trigger, so a finished
run has no popover. Post-run inspection lives in the chat timeline.
