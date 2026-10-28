---
code_file: frontend/src/components/dashboard/SessionSection.tsx
last_verified: 2026-08-14
stub: false
---

## 2026-08-14 — AvatarDot 只算一次身份

`senderIdentity(seed).dot` 和 `senderIdentity(seed, display).initials` 是同一个身份算了
两遍，而且**两次参数还不一样**——第一次没传 display。现在算一次。

# SessionSection — dashboard 的会话区

## 2026-08-12 — 头像配色改用共享的 [[senderIdentity]]

本文件此前自带一份哈希调色板（`colorForSeed`）。抽取时发现它与 `AgentInboxPanel` 的那份
**色序已经不同**（位置 5-7 相反），所以同一个 agent 在两个页面本来就可能是两种颜色。
现在两处共用一份实现，颜色成为跨界面稳定的**身份**。

哈希算法逐字节保持不变，所以本页面上任何 agent 的既有颜色都不会移动。
