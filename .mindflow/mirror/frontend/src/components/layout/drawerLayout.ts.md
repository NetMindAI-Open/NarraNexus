---
code_file: frontend/src/components/layout/drawerLayout.ts
last_verified: 2026-09-11
stub: false
---

## 2026-09-11 — per-agent first view: new agents open on Artifacts (Owner)

Owner report: a new agent showed no Artifacts panel, so its explainer was never
seen. `shouldAutoOpenFirstRun` only ever fired for a brand-new profile (any
drawer key ⇒ existing user), so an existing user's NEW agents never opened it.
Added `DRAWER_AGENT_SEEN_KEY` (JSON array of agent ids, bounded to
`MAX_SEEN_AGENTS` = 500, oldest dropped) with the same read-only/mark pair as
the first run: `shouldAutoOpenForAgent(storage, agentId, isSmall)` is true for
an agent not in the list (false with no agent, on a small viewport, or when the
store throws — a broken store must not open the drawer on every view; a corrupt
value reads as "nothing seen" once and is rewritten by the mark), and
`markAgentDrawerSeen` appends idempotently from an effect. The pin state is NOT
touched: pinned is already the default and only an explicit unpin ('0') is
respected. The global first-run coach is unchanged.

# drawerLayout — 书签抽屉的尺寸与持久化策略

从 [[MainLayout]] 抽出的纯函数模块:localStorage 键(含独立的
`DRAWER_FIRST_RUN_KEY`)、`DEFAULT_DRAWER_PX`、`maxDrawerPx(vw)` =
`clamp(MIN, min(0.6·vw, vw−672))`(672 = 侧栏 272 + 聊天最小 400)、
`clampDrawerWidth`、`readInitialDrawerPinned`(缺省钉选,只认显式 '0')、
**首跑对**:`shouldAutoOpenFirstRun`(只读,render 安全;小视口 false 且
不烧标记)+ `markFirstRunSeen`(effect 里写)。首跑=「真·新用户」:任一
既有抽屉 key(OPENED_ONCE/PINNED/WIDTH)存在都视为老用户直接 false——
教学卡只给没见过抽屉的人,存量用户在功能上线时不被重新引导。首跑标记是
自己的 key,手机访问不烧桌面教学。**契约**:三个信号 key 必须都由用户
动作写入——WIDTH 只在拖拽释放时持久化(消费方守约,见 [[MainLayout]]);
若哪天有代码在挂载期无条件写它们,老用户判据即刻失真。
改上限/默认值只动这里;MainLayout 只消费。测试 drawerLayout.test.ts 钉住
「新档案默认钉选」「大屏可 ≥ 半屏」「永不挤掉侧栏+聊天最小宽」。
