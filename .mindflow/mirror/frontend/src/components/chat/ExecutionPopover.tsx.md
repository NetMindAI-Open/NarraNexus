---
code_file: frontend/src/components/chat/ExecutionPopover.tsx
last_verified: 2026-07-03
stub: false
---

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
