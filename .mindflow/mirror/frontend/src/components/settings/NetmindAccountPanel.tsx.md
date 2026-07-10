---
code_file: frontend/src/components/settings/NetmindAccountPanel.tsx
last_verified: 2026-07-06
stub: false
---
 
## 2026-07-06 — recent activity collapsed by default

The activity list is now behind a collapsed toggle (`showActivity`, default false):
by default only a clickable "Recent activity ›" header shows; clicking expands the
settled-records list. Keeps the panel clean by default while the ledger stays one
click away. The toggle only renders when there is at least one settled record.


 
## 2026-07-06 — activity list hides `pending` records

Recent activity now filters out `status==='pending'` (settled ledger only). Every
abandoned checkout (opened, not paid) leaves a NetMind `pending` recharge record
that only flips to `failed` ~24h later when the Stripe session expires — showing
them piled up stale "pending +$X" rows. In-progress payment is already surfaced by
the live "waiting" state, so the ledger only shows succeeded/failed.


 
## 2026-07-05 — recharge: stop-waiting escape + generation-based cancel

If the user closes the Stripe window without paying, the by-session status stays
`pending` (Stripe sessions don't expire on tab close), so the panel would sit in
"waiting for payment…" until the 3-min poll deadline with the button disabled. Added
a "Stop waiting" action that returns to idle immediately. Implemented via a
`rechargeGenRef` generation token: each top-up captures a gen; the poll loop bails
whenever the current gen moves on (stop-waiting bumps it, so does a new attempt),
so a stale loop can never overwrite the UI or block a retry. Replaced the old
`rechargePollRef` overlap guard (which could wedge a just-cancelled loop and block
the next attempt for up to one poll interval).


 
## 2026-07-05 — top-up UI (Phase 4, module E)

Added an "Add credits" block under the balance hero: preset tiers ($5/$10/$20/$50) + custom
amount → `api.recharge` → openExternal(checkout_url) → bounded poll of `api.rechargeStatus`
until succeeded/failed → on success reload() refreshes the balance + activity. Three states
(processing/success/failed) mirror the subscribe flow; same synchronous double-click +
non-overlapping poll refs. Amount ≤0 is blocked client-side before any call.



# NetmindAccountPanel.tsx — 「NetMind 账户与订阅」面板（Phase 1 骨架）

## 为什么存在

云端版 Settings 里第一次把用户 NetMind 订阅状态搬到界面（模块 A），并承载沙盒
限免声明（模块 G）。是 B/C/D/E/F 后续模块共用的面板家。刻意**镜像**
[[QuotaPanel]]：cloud-mode 门禁 + 不适用时 `return null`（无布局抖动）。

## 上下游

- 数据源：`api.getSubscription()`（[[api]] → [[billing]] `/api/billing/subscription`）。
- 挂载点：[[SettingsModal]] 的 `billing` 导航 section。
- 文案：`settings.netmind.*`（en/zh locale + 组件内英文默认，铁律 #1）。

## 四态（S0–S3）

- **S0** 本地模式（`mode !== 'cloud-web'`）→ 整个隐藏。
- **S1** `subscription == null` → Free。
- **S2** `status==ACTIVE && auto_renew` → Pro 生效。
- **S3** `status==ACTIVE && !auto_renew` → 已取消、周期内有效、到期降级。

## 踩过的坑 / 设计决策

- **文案必须英文源 + i18n key**（铁律 #1）：组件内联默认用英文，中文在 zh.json。
  最初写成中文内联默认被 code review 拦下（英文用户会看到中文）。
- **billing 的 401 不能触发全局登出**：见 [[api]] `isBillingEndpoint` 跳过——
  否则打开面板（NetMind token 缺失/过期）会把有效 NarraNexus 会话也登出。
- **空 token 早退**：`api.getSubscription()` 无 token 直接 throw，面板落 error 态，
  不发空头round-trip（安全审查 H-1）。
- `resolveState` 把非 ACTIVE 状态（EXPIRED/PAST_DUE/未来态）暂归 free 显示——
  Phase 2 待 NetMind 文档明确后再补专门 UI。
- 余额/消耗（模块 B）不在此面板 phase：数据源 user-fee-info 目前 dev 403，是 B 的门禁。

## Phase 3 新增（2026-07-02）— 订阅操作（模块 C/D）

- S1 加"订阅 Pro"按钮 → `api.subscribe()` → `platform.openExternal(checkout_url)`
  → **轮询** `/me` 直到 ACTIVE（上限 180s）。
- S2 加"取消"（`window.confirm` 二次确认）→ `cancelSubscription` → 刷新。
- S3 加"恢复自动续费" → `reactivateSubscription` → 刷新。
- **C3 缓解**：外部支付无确定回流信号（尤其桌面），故 **window focus 时也刷新** +
  轮询有界。`busy`/`polling`/`actionError` 三态反馈。
- reactivate 语义（恢复续费 vs 重订）**待 NetMind 确认**；已知能调通（401 存在性）。

**审查加固**：① `busyRef`（同步锁）—— React state 异步，双击会在 disabled 重渲染前
二次触发 → 重复 Stripe checkout；ref 同步翻转才是真守卫（质量 HIGH）。② `pollingRef`
—— 禁止两个 poll 循环重叠（focus-refresh + poll 竞态）。③ reactivate 加 `window.confirm`
（涉及钱）。④ 轮询超时给 `pollTimeout` 提示，不静默消失。

## Phase 2（2026-07-02）— 余额区（模块 B 降级版）

`load()` 并行拉 `getFeeInfo()`（独立 try，fee 失败不影响订阅态显示，余额区隐藏）。
展示 free_credit（当前余额）+ monthly_free_credit + eligible/欠费 + **扣费顺序硬文案**
（先订阅→再余额→停）+ 降级说明。G1：无本周期消耗、无订阅/余额拆分。

**Phase 2 审查加固**：`FeeInfo` 所有字段改可选（后端 verbatim 代理 NetMind、无 schema
校验）；余额区 render 全用可选链 + 兜底（`fee.metrics?.free_credit ?? '—'`、
`fee.eligible === false`、`fee.checks?.has_arrears`）——否则部分/畸形的 200 会在
render 阶段崩，反而把上面订阅态也带崩（fetch 的 try 保护不到 render，质量 HIGH）。
`load()` 改 `Promise.allSettled` 并行拉 subscription + fee，各自独立处理保持隔离。

## Phase 5（2026-07-02）— 使用此订阅（模块 F 入口）

加“使用此订阅”卡片 → `api.useSubscription()`（POST /api/providers/use-subscription）。
成功→绿字“已接入,去选模型”；失败→红字错误（含功能开关关时的 403 文案）。busyRef 同步锁。
后端功能开关默认关（C1 待确认），现在点会返 403 提示；开关一开即通。

## G1 增强（2026-07-03）— 最近流水

`load()` 的 allSettled 加 `getRecords()`（独立，失败则流水区隐藏，不影响订阅/余额）。
渲染「最近流水」列表（日期·类型·±金额·状态，income 绿色）。补 G1 消费明细缺口。

## UI 重构（2026-07-03）— 单卡片 + NetMind.AI Power 定位

- 从"每个 phase 一个独立框"整合成**一个卡片**：头部品牌 `NetMind.AI Power` + 套餐徽章 →
  余额 hero（当前余额大字）→ 套餐状态 + 主操作（订阅/取消/恢复）→"用此账户驱动"入口 →
  最近流水 → 灰色脚注（扣费顺序 + 范围说明 + 沙盒声明）。
- **文案定位**：主体明确是 **NetMind.AI Power 的套餐/额度**，不提"Nexus 套餐"。
- **范围说明（scopeNote）**：额度只覆盖 **LLM API** 调用；算力（GPU）等定价另计——
  避免用户以为这次支付能解决算力定价。
- i18n netmind 块随之重写（en/zh），去掉不再用的 key（title/deduct1-3/balanceTitle/
  balanceDegraded/proCancelled/autoRenewOn），加 subtitle/badgeFree/badgeCancelled/
  deductOrder/scopeNote。
