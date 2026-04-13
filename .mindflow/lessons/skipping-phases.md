---
title: 跳过 DEV 双重审查 / TEST Part B / DOC 同步
applies_to: [dev, test, doc, orchestrator]
severity: high
recorded_at: 2026-04-12
source: Agent Dashboard 开发 session 用户反馈
---

# Lesson: 沉默跳过 phase 是累犯问题

## 事件

2026-04-10 Agent Dashboard 开发 session 中，实际执行时出现三处跳过：

1. **DEV 阶段**：主 agent 直接实现，未派 per-task implementer subagent，也未派 spec_reviewer / quality_reviewer
2. **TEST_A → IMPROVE 之后的 TEST_B**：完全跳过（PRD 验收 + user_in_coding 实测 + 终审）
3. **DOC 阶段**：mirror md 更新被 PostToolUse hook 阻止后，没补回来就 transition 到 DELIVER

## 根因

- Skill 描述被读一次就忘，几轮对话后主 agent「凭感觉」走流程
- 没有强制**证据落盘**的机制——跳过和做了看起来一样
- Hook 干扰了状态写入时，主 agent 误以为「跳过也没事」而不是「修 hook」

## 教训

**Flexibility on method, not on evidence.**

- 主 agent 直接实现 DEV 任务是**可以**的（任务简单、强依赖、纯调 bug），但**必须**给每个 task 留证据（commit hash / 测试路径 / 绿色输出）
- TEST Part A（代码验）和 TEST Part B（需求验）不是一码事，任何借口都不能合并
- Mirror md 同步是铁律 #10，没有「下次再补」

## 可验证的规避措施

- `mindflow-phase-transition-check` skill 在每次 phase 切换时强制证据检查
- `hooks/phase_transition_guard.sh` 在 state 文件被写入时挡住缺证据的 transition
- DEV 阶段产出 `.mindflow/state/dev_log.md`，每个 task 一条记录
- TEST_B 产出 `.mindflow/state/test_b_report.md`，PRD 逐条勾选

## Red Flag 原话（将来再冒出来时立刻 STOP）

- "任务都很简单，直接写完就完事"
- "Part B 跟 Part A 重复了，跳过"
- "mirror 下次再补"
- "Hook 挡了我写 state，先跳过吧"

遇到就停，不要辩护，不要继续。
