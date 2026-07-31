---
code_file: frontend/src/components/chat/SegmentedReply.tsx
last_verified: 2026-07-30
stub: false
---

## 2026-07-30 (r2) — 流式段 plain text + defaultOpen

- **流式段不走 Markdown**：每 delta 全量重解析拖死主线程（与 ThinkingBlock
  2026-05-12 同一教训），表现为「蹦几个字→卡住→整段一次性出来」。流式期间
  渲染 plain pre-wrap + 光标，落定切 Markdown（旧 ReplyBlock 同款取舍）。
- **defaultOpen**：历史气泡点一次「View reasoning」已经是一次点击，fetch
  完落在又一层折叠入口上等于要点两次（Owner 反馈）。fetch 路径传
  defaultOpen 全部展开，仍可手动收起；新鲜消息（stopStreaming 带 segments）
  保持折叠入口一次点开。

# SegmentedReply.tsx — 把 Segment[] 渲染成 agent 实际说话的那几次

## 为什么存在

一轮可能说多次话（n 次工具调用里有 m 次是回复用户）。后端仍是一轮
一条记录；这个组件把那一条渲染成 m 个「说话」，每个可带上导致它的
过程。段的切法由 `lib/segmentTurn` 决定——这里只负责画。

## 设计决策

- **同一个组件服务直播与历史**，差别只有 `showProcess` 一个开关：
  直播中 false（过程在 ProcessPanel，两处都画就重复）；结束后 true
  （面板已卸载，过程折叠回各段自己的气泡上，`<TurnTimeline>` 渲染）。
- **展开状态记在本组件**（按段索引）：父组件在流式期间每个 delta 都
  重渲染，状态放这儿才不会被重置。
- **helper_llm 恢复徽标**（2026-07-30 自 TurnTimeline 的 ReplyBlock
  迁入）：`reply.via` 为 `helper_llm_no_reply`（或 legacy
  `helper_llm_fallback`，2026-05-25 改名前的持久化值，不 backfill）
  显示 info 徽标；`helper_llm_after_error` 显示 warning 徽标。i18n
  keys 沿用 `chat.timeline.helperFallback*` / `recoveredAfterError*`。
- **流式光标**只给最后一段（`isStreaming && isLast`）。

## Gotcha

- `reply=null` 的段（零回复轮次）不渲染气泡但保留过程入口——过程
  不因没说话而丢失。
