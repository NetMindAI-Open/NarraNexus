---
code_file: frontend/src/components/chat/ComposerFastToggle.tsx
last_verified: 2026-08-14
stub: false
---

# ComposerFastToggle.tsx — composer 工具行的 fast-mode 开关

## Why it exists

chat fast mode 的 UI 入口：闪电图标 + 「Fast/极速」小按钮，停靠在 composer
tools row 右侧、[[ComposerModelBadge]] 左边。开启后该 agent 的下一个 chat
turn 在 WS 首包带 `fast_mode: true`（AgentRuntime 侧映射成 TurnProfile：
BM25 top-1 叙事 + fast 框架 + 低 reasoning effort）。

## Design decisions

- **纯展示组件**：状态在 [[ChatPanel]] 经 [[useFastMode]] 持有，本组件只收
  `enabled/onToggle/disabled`——与 AudioRecorder 的受控风格一致，可独立测试。
- **a11y**：`aria-pressed` 镜像开关态（测试锁定）；`disabled` 阻断切换。
- 开启态用 carbon 强调色（同发送按钮活跃态），图标 fill-current 加强可辨。
- i18n key `chat.fastMode.*`（10 个 locale 全量翻译——composer 行里不能出现 9 译 1 英，#307 🟡5）。tooltip 如实
  披露本轮实际发生的三件事（切换快速框架 / 轻量记忆检索 / 低推理档位）
  ——framework_override 覆盖用户 per-agent 框架选择是 spec 接受的偏差
  （与 voice F28 v1 同款 knobs），不许只写「更快」（预审 I3）。

## Upstream / Downstream

- **Upstream**: [[ChatPanel]] tools row。
- 测试: frontend/src/components/chat/__tests__/ComposerFastToggle.test.tsx
