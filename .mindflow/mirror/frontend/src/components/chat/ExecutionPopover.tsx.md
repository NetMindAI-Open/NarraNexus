---
code_file: frontend/src/components/chat/ExecutionPopover.tsx
last_verified: 2026-06-11
stub: false
---

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
