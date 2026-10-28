---
code_file: frontend/src/stores/uiStore.ts
last_verified: 2026-09-03
stub: false
---

## 2026-09-03 — `openPanel`：不带开关语义的「打开面板」

`requestPanel` 一直是**开关**：MainLayout 的 effect 里 `prev === pendingPanel`
就把抽屉关掉。这对「用户点了面板入口」是对的，对「调用方需要这个面板最终是打开
的」是错的 —— 创建工作室用它亮出配置面板时，如果那个面板恰好已经是当前 tab，
第二次进 studio 会把它**关掉**（现象就是「点了 Create with AI 但什么都没弹」）。

于是拆成两个意图，用 `pendingPanelMode` 表达：

| action | mode | 语义 |
|---|---|---|
| `requestPanel` | `'toggle'` | 用户点入口，再点一次收起 |
| `openPanel` | `'open'` | 必须打开，永不关闭 |

`clearPendingPanel` 会把 mode 一起复位，避免残留意图泄漏到下一次请求。
测试钉在 `components/bookmarks/__tests__/builderTab.test.tsx`。

## 2026-08-30 — `interimNarration`：独白提级的显示偏好

独白「进度」档（A′，见 [[monologueTier]]）的开关，缺省**开**，
`localStorage` 键 `interim_narration_v1`，只有显式 `'0'` 才关（新档案、
storage 不可用都落到缺省）。

**只有 setter，没有 toggle**：照抄 `toggleSidebar` 写了一个
`toggleInterimNarration`，但零调用点（Settings 用的是
`setInterimNarration(!interimNarration)`），而它等于把「键名 + `'1'/'0'`
编码 + try/catch」这套持久化规则抄成第三份不可达副本。review 第 3 轮删掉了。
真要 toggle，就让它走 `setInterimNarration`，持久化只留一份。

读侧统一走 [[useNarrationTier]]，不要在组件里直接 `useUIStore` 取这个字段
——三个渲染面各读一次就是三个「改缺省值时会漏掉一个」的点。

**为什么不是后端 env flag**。对照 `NARRATIVE_MERGED_ROUTING_ENABLED` 的治理
惯例——那个 flag 存在是因为它切换 **LLM 调用结构**、灰度期要并行比路由质量、
且线上运行中无法靠发版回退。本项一条都不满足：调用数/prompt 字节/token 全同，
内容完全相同，只有色阶不同，回滚 = revert 一个前端 commit。它要治理的是
「读者觉不觉得吵」——那是**用户偏好**，不是平台风险。对标 Hermes 自己的
`display.interim_assistant_messages` 也是用户侧显示配置。

关掉 = 回到 A′ 之前的观感：同样的块、同样的文本、同样的顺序，只是色阶退回
receded。**任何档位下都不增删一个字**。

**入口在 [[PersonalizationSettings]]**（Settings → Personalization），和
theme / language 同一个面板——该文件头就写着「everything configurable belongs
to this page」。别再往 sidebar 账号气泡里加第二个设置面（那正是那个面板存在的
原因）。首轮 review 抓到过一次「setter 是死代码、偏好只能永远为 true」：
偏好和它的入口必须同一单落地，否则 `TurnTimeline.narrationTier` 里两条
「关掉后…」的用例断言的是不可达状态。

## 2026-08-06 — Chat UI v4:collapse + palette 状态入驻

新增 `sidebarCollapsed`(持久化 localStorage `sidebar_collapsed_v1`;
v4 收起=整栏隐藏,展开按钮在 ChatPanel 头部 / MainLayout chip,因此必须
跨组件共享)与 `paletteOpen`(⌘K palette 唯一宿主在 MainLayout,触发器
分布在 Sidebar 搜索钮和移动端 TopBar)。mobileNavOpen / pendingPanel 不变。

# stores/uiStore.ts — shared UI-chrome state with no backend

## Why it exists

A small zustand store for layout-chrome state that several sibling components
share but no backend cares about. It exists so these booleans/ids live in one
place instead of being prop-drilled across three siblings ([[TopBar]],
[[MainLayout]], [[Sidebar]]) that have no natural common parent to hold them.
It is intentionally separate from [[configStore]]/[[runtimeStore]] because this
is ephemeral view state, never persisted or synced.

## How it works / design

- Two concerns: (1) `mobileNavOpen` — the off-canvas agent-list drawer on
  small screens; [[TopBar]]'s hamburger toggles it, [[MainLayout]] renders its
  backdrop, [[Sidebar]] closes it on navigation. (2) `pendingPanel` — a context
  panel requested from [[CommandPalette]].
- Upstream/producers: [[TopBar]] (`toggleMobileNav`), [[CommandPalette]]
  (`requestPanel`). Consumers: [[MainLayout]] / [[Sidebar]] read
  `mobileNavOpen`; [[ChatView]] reads `pendingPanel`, opens the matching drawer,
  then calls `clearPendingPanel`. Re-exported via [[stores]] `index.ts`.
- `pendingPanel` is the mobile entry point for context panels (awareness / jobs
  / …) because the right bookmark strip is hidden on mobile — ⌘K is the only way
  in, and this store is the hand-off channel.
- Gotcha / design decision: `pendingPanel` is typed as a bare `string` rather
  than the real `AtomicTabId` on purpose, to keep this store free of component
  imports (it stays a pure leaf store). The consumer is responsible for
  clearing it so a request fires exactly once.
