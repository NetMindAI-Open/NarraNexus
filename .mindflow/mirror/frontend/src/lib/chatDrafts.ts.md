---
code_file: frontend/src/lib/chatDrafts.ts
last_verified: 2026-08-14
stub: false
---

# chatDrafts.ts — 半句话不该因为你离开就没了

## 为什么存在

聊天输入框只有一份本地 `input` state，切 agent 或刷新页面就把用户打了一半的东西丢掉。
草稿按 agentId 存进 localStorage，于是**切换和整页重载都活得下来**。空草稿是删除而不是
存空串——否则这张表只增不减。

## 2026-08-14 — 团队房间也有草稿，且用自己的存储键

房间的输入框此前**完全没有草稿**：换个房间或者导航走，写了一半的内容直接消失。
在一个"把活交出去然后离开"就是设计意图的空间里，这恰好丢的是产品的另一半。

`getTeamDraft` / `setTeamDraft` 用**独立的 storage key**（`narra-nexus-team-drafts`），
不是在 agent 那张表里加前缀。两个理由，决定性的是第二个：

1. 共享一张表意味着 team id 和 agent id 相等时会互相覆盖——而这件事能不能发生，从代码
   里无法确认。（同样的判断见 [[unread.ts]] 的已读水位线。）
2. 复用那张表但换 key **格式**，会把用户此刻正打着一半的草稿全部变成孤儿——为了省一个
   localStorage 键，付一次迁移的摩擦。

## 谁在用

- agent：[[Composer.tsx]]（防抖写入 + 卸载时 flush，`key={agentId}` 保证换 agent 是重挂载）
- team：[[TeamChatPanel.tsx]]。房间那边**不是重挂载**——路由参数变化时组件还是同一个
  实例，`teamId` 和 `text` 在不同的 commit 更新，所以面板额外持有一个"这段文字属于哪个
  房间"的 ref。少了它，切房间后的第一次保存会把上一个房间的话记在新房间名下。
