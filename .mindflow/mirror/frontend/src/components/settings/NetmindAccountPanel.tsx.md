---
code_file: frontend/src/components/settings/NetmindAccountPanel.tsx
last_verified: 2026-07-13
stub: false
---

## 2026-07-13 — 门禁改挂 per-user Power 信号(本地双模式)

面板可见性从 `mode === 'cloud-web'` 改成 `isPowerUser = !!configStore.netmindToken`
—— 即"本会话是不是 Power 账号"(持有 NetMind loginToken),而非部署模式。于是本地
双模式下 Power 用户能看到面板,纯本地用户名用户看不到(返回 null)。`useRuntimeStore`
import 已移除。S0 测试相应从 `mode='local'` 改成 `netmindToken=''`。

## 2026-07-12 (latest) — 连接行归入身份组(修版式拥挤)

连接行原在"余额 hero 与 runway 之间",available 变小字后紧贴"免费额度"行,拥挤。
改为三段分组:**身份组**(账号 + 套餐 + 连接状态)→ 分隔线 → **金额组**(余额 hero
+ runway 明细连成一片)→ 动作(管理)。连接状态是"我是谁/怎么接入"的一部分,归身份组;
金额块不再被打断。注:本地 quota 关时 runway 整体隐藏,该相邻只在 dev(quota 开)出现。

## 2026-07-11 — 连接态区分 driving / available(修误导)

Owner 在 dev 发现:`connected` 只表示"存在 netmind 卡",但云端登录自动注册,人人
都有卡——哪怕在用自己加的 provider。此时绿 ✓"已就绪,无需配置"会让人误以为正跑在
NetMind 上。修:`refreshNetStatus` 读 `slots.agent.config.provider_id` 是否指向
netmind-source provider,拆成:
- **driving**(agent 槽=netmind)→ 绿 ✓"正在用你的 NetMind.AI Power 账户运行 ——
  无需配置"(此时声称"运行中"才属实)。
- **available**(有 netmind 卡但 agent 槽是别的 provider)→ 中性灰字"已接入但未启用
  —— 当前由你自己的 provider 驱动。可在 Model Defaults 切换"(不给绿 ✓、不声称运行)。
- not_connected / error / checking 不变。
i18n:+netDriving/netAvailable,删 connectedManage。判定用 **agent 槽**(驱动
NarraNexus 的主 LLM);槽空/取不到 → 保守判 available(宁可不声称也不误导)。

## 2026-07-11 — 顶部重排:Account 身份 + 套餐带解释 + 余额 hero + 三池诚实

Owner 走查:标题叫 "Account & Subscription" 却无 Account、套餐是右上角无解释裸徽章、
余额只是小行。重排卡片顶部:
- **账号行**(补 Account):`displayName · email`(读 [[configStore]]),email 空则隐藏。
  也是"$5 进哪个账号"困惑的解药。
- **套餐行**:定义列表 `套餐: [徽章] + planExpl` —— Free"免费版·用量按余额扣费" /
  Pro"会员·有效至X"(planExplProActive)/ 取消"到期后降级"(expiresDowngrade)。
  徽章从 header 右上角移进此行;topStatus() 删除。
- **余额 hero**:`free_credit` 放大成锚点(34px);label 按有无赠额切
  balanceUsable/currentBalance。
- **三池诚实呈现**:接口把套餐赠额+充值合并进 `free_credit`,只有平台免费额度独立。
  故 hero=合并余额(池2+3),[[NetmindRunwayView]] 只显示免费额度条 + 赠额行
  (标"已计入余额",非可加数字);**不摆三个独立可花数字**(避免用户以为能加起来花)。
  余额行从 RunwayView 移到 hero;单池(仅余额)时 flow line 隐藏(#3)。
- **连接行**:去掉"切换驱动服务商"指引(#1),只留"已就绪,无需配置";位置在余额
  hero 之下、runway 之上。
- **Pro 套餐真实文案**([[NetmindUpsellCard]]):卡名"NetMind Pro" + $19/月 +
  "OpenAI、Anthropic 等模型最高5折" + "零平台服务费" + "每月19万Credits(≈$19)"。
  删旧的"热门模型5折/100+模型库/Includes...credits"。文案非 API,plan 变动需手改。
- i18n:+account/plan/balance/upsell 等键,删 readyPro/planValidUntil/upsellPerk
  Member/Library/GrantLine/flowFreeNoTier;en/zh 65 齐平。
- **待确认**(记入清单):`free_credit` 是否真含未用完的月度赠额(NetMind 侧行为),
  与扣费顺序一起等 xiyue 核对。

## 2026-07-10 — plan × runway 重设计:吸收 QuotaPanel、渐进披露、单一主 CTA

动机:两个平级花钱按钮(Subscribe/Recharge)+ 三种"钱"分居两卡让新用户决策瘫痪
($19 订阅与 $19 充值 credit 等值,差异只在会员价+模型库,旧 UI 没说)。重构为:

- **两个正交维度**:plan(free/pro_active/pro_cancelled,沿用 `resolveState`)×
  runway(healthy/low,新纯函数 [[netmindRunway.ts]] `deriveRunway`)。
- **QuotaPanel 已删除**,免费额度视图 + `prefer_system` 开关吸收进本卡:
  `load()` 的 allSettled 加 `getMyQuota()` + `getPlans()`(各自独立失败隔离);
  runway 视图渲染在 [[NetmindRunwayView]](免费额度条+赠额+余额+扣费顺序一句话+
  「Free tier first」开关)。开关锁定规则:exhausted 时只锁 ON 方向(后端
  "OFF is always allowed" 409 守卫的镜像)。
- **渐进披露**:healthy 态零花钱按钮(subscribe/top-up 收进 `showManage` 展开);
  low 态才升起唯一主动作 —— free→[[NetmindUpsellCard]](会员价+模型库价值主张,
  价格=`monthly_grant_usd` 动态取,决策 A;充值降级为文字链接),pro→充值直出,
  pro_cancelled→恒 Resume。
- **文案**:去贬义(`free`/"not subscribed" 删除);扣费顺序从页脚 footer 移进
  runway(flowFree 两池 / flowPro 三池;权威顺序待与 xiyue 核对——若不同只改这
  两个 i18n key);页脚只剩 scopeNote + sandboxNotice。外链 `PRICING_URL` 指
  netmind.ai/pricing(深度内容不进面板)。
- i18n:netmind 块 +26 新 key、-5 死 key(free/proActive/validUntil/deductTitle/
  deductOrder);`settings.quota.*` 整块删除。en/zh 59 key 齐平。
- 测试:40 用例(适配映射 + plan×runway 矩阵 + prefer 开关 + plans 降级 +
  toggle 双击守卫 + 未知 quota 态中性文案);测试 i18n mock 升级为支持
  `{{var}}` 插值以断言完整文案;afterEach restoreAllMocks(confirm spy 卫生)。
- 模块 F 状态**按信息价值分层**(同日,Owner 走查):`not_connected`(唯一
  可行动的连接态,agent 跑不了)提到顶部状态行下方、警示色;`connected/checking`
  (管理性确认,无需行动)留在下方——"放心"职责归顶部 topStatus,避免双绿勾叠加。
- 连接信息**彻底收敛成一条**(Owner 走查定案):删掉 `topStatus` 里的 `readyFree`
  "正在用 NetMind 运行"(它按 runway 门控、且 C 场景会误称在用 NetMind);连接
  信息只剩 `connectionStatus()` **一处**,由真实 `netStatus` 驱动,是全卡**唯一
  的绿 ✓**。四态:connected=绿✓"已就绪,无需配置·去 LLM 服务商切换驱动服务商"
  (不声称在用谁)/ checking=灰 / **error=灰"暂时读不到,请刷新"** / not_connected=
  警示。`topStatus` 只剩套餐(Pro 用中性文字"Pro member · valid until X",无 ✓,
  避免与连接 ✓ 撞第二个绿勾;Free 不显示,徽章足够)。连接行移到卡顶(runway 之上)。
- **error 从 not_connected 拆出**(review):getProviders 请求失败 = 瞬态 → 提示
  刷新,不再误导"重新登录"(重登修不了网络抖动);读到了但无 netmind 卡才是
  not_connected → 才提示重登/手动添加。i18n:+netStatusError、-readyFree。topupOrLink 从只开不关改为 toggle(删 onRevealTopUp prop)。
- UI 走查修复(同日,Owner 看真实 prod 数据后):`notEligible` 警告在低额引导
  可见时(runway low 且非 pro_cancelled)**不再单独渲染**——eligible=false 必然
  触发 low,引导语已用人话说了同一件事,叠加系统腔警告读起来像报错且字号不一
  (12px vs 14px);仅 pro_cancelled(动作区谈续费不谈额度)保留。lowChoose 改
  "余额不足";upsellGrantLine 去掉 {{period}}(与价格行重复出"每月…/月"病句)。
- review 修复(同日):动作区抽为 [[NetmindActionZone]](面板 784→665 行);
  togglePrefer 加 preferBusyRef 同步守卫(与 busyRef 同模式);free×low 文案
  按 freePct===0 门控(exhaustedChoose vs 新 key lowChoose);
  `SubscriptionPlan.features/quota_limits` 字段改可选(verbatim 代理现实)。

## 2026-07-10 — 模块 F 改为「只读状态」，删掉 mint/connect 按钮

云端登录已在后端自动 register NetMind provider（见 [[netmind_provisioner]] +
[[auth]]），所以面板这里**不再 mint、不再有按钮**。原来的连接状态机
（`connect`/`connectNetmind`/`resolveConnection`/`classifyConnectError`/
`other_provider` 切换按钮/重试按钮）**全部删除**，换成读 `api.getProviders()` 的三态
只读状态 `netStatus`：`connected`（存在 `source==='netmind'` 的 provider）/
`checking` / `not_connected`。选择"由哪个 provider 驱动"归 LLM Providers 区。
面板不再调 `api.useSubscription()`（`api.ts` 仍保留该方法，供兜底路由用）。i18n
去掉 `useTitle/useDesc/useSubscribeBtn/useSubscribeOk/connectedStatus/connecting/
useDescSwitch/connectRetry`，加 `connectedManage/checkingStatus/notConnected`。
（本条取代下方 2026-07-02「Phase 5」与旧版 auto-connect 描述。）

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
