---
code_file: frontend/src/components/bookmarks/DrawerCoachMark.tsx
last_verified: 2026-08-19
stub: false
---

# DrawerCoachMark — 抽屉首跑教学卡

新用户首次进入时 artifacts 面板默认钉选打开(要先看见东西落在哪),这张
一次性卡片解释怎么拿回屏幕:unpin(变浮层)与 close(从聊天头重开)。
经 [[BookmarkDrawer]] 的 `banner` 插槽渲染;显隐与 once 持久化归
[[../layout/MainLayout]](opened-once 键复用,不新增存储)。文案
`bookmarks.coach.*` ×10 locale。
