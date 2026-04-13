---
title: 被 hook 挡住时不要跳 phase
applies_to: [orchestrator, dev]
severity: medium
recorded_at: 2026-04-12
source: Agent Dashboard 开发 session 用户反馈
---

# Lesson: 和 hook 打架不是跳过 phase 的理由

## 事件

`.mindflow/state/current_project.json` 被 PostToolUse hook 拦截（hook 配置有 bug：未排除 `.mindflow/state/**` 路径），导致 `notification.lark_target` 字段没写入。主 agent 没有停下修 hook 或绕道，而是继续 DELIVER，靠 session 记忆硬编码 Lark open_id。

## 根因

遇到 hook 阻挠时下意识选了"跳过"而非"修根因"——违反 CLAUDE.md 铁律 #5「不只治标，要治本」。

## 教训

- Hook 阻挠 = 基础设施问题，**不是** phase 跳过的授权
- 状态文件写不进去 → 立刻报告并暂停，不要继续
- 如果必须 workaround，也要把 workaround 记录到 `.mindflow/state/` 里，避免信息只存在 session 记忆

## Red Flag

- "Hook 挡了我写 state，先跳过吧"
- "靠会话记忆就够了"
- "反正我知道发给谁"

出现以上任一想法 → 停下 → 修 hook 或明确记录阻碍 → 再继续。
