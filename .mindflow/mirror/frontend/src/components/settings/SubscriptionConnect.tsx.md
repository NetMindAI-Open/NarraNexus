---
code_file: frontend/src/components/settings/SubscriptionConnect.tsx
last_verified: 2026-09-09
stub: false
---

## 2026-09-09 — 状态行三态：`expired` 有了消费方（B-25 后半，复审 I2/M2/M7）

后端 `/claude-status`（与 `/codex-status`）对过期 token 把 `logged_in` 掰成 false 并给
`expired: true`，但前端从没读过这个字段：`CliStatusLine` 只有两态，过期时走
`notLoggedIn`（"从没登录过"的文案），而过期时间行和 email 的门是 `logged_in`——**最需要
线索的时刻恰恰把线索藏起来**（修之前"已登录 + Expires 3/1"撒谎但有线索，修之后"未登录"
无日期无邮箱）。现在：

- `CliStatusPayload.expired?: boolean`（[[../../lib/providersApi.ts]]；可选，两条 status
  路由和测试 mock 早于这个字段）。
- `CliStatusLine` 第三态：黄点 + `settings.provider.sessionExpired`，仍渲染 `email` 与
  `expires_at`（门改为 `logged_in || expired`）。`data-testid="cli-status-line"`。
- `ProviderRecordRow` 加 `sessionExpired` prop：记录存在但 CLI 会话已过期时，"✓ 已添加"下
  方多一行 `settings.provider.sessionExpiredHint`（否则与上面的"已过期"自相矛盾）。claude 卡
  传 `expired && !claudeTokenConnected`——setup-token 运输层绕过 CLI 会话，token 记录不受
  CLI 过期影响；codex 卡直接传 `expired`。
- `formatExpiresAt` 的秒/毫秒阈值从 `1e12` 改为 `EPOCH_MS_THRESHOLD = 1e11`，与
  `backend/routes/providers.py` 同一个常量（原来 1e11–1e12 之间两边解释相反）。
- 两个新 i18n 键落全部 10 份 locale（locale-parity 门禁）。

`ProviderPickerModal.tsx` 把 payload 收窄成 `{cli_installed, logged_in}` 但只渲染
`cli_installed`（登录态只决定是否显示 Login 按钮，过期时 `logged_in` 已为 false，行为正确），
本批不动。

# SubscriptionConnect.tsx — Claude Code / Codex 订阅连接卡(从 ProviderSettings 抽出)

## 为什么存在

P0(2026-08-28):landing 只配订阅走不通。订阅登录原本埋在三层之下
(SetupPage 的 Advanced 折叠 → ProviderSettings 的 add modal → Sign in
tab),主卡 OneKeyOnboard 又是纯 API key 表单——订阅用户把 landing 读成
"必须有 API Key"。抽出后 SetupPage 把它放在**折叠区展开后的首位**(第一版做成一等并列卡
且连接即跳转,Owner 手测后否决——保留原页面格式、订阅默认收在折叠里、
不自动跳转),ProviderSettings 的 Sign-in tab 原位引用,逻辑单份。

## 两层状态,严禁混淆(继承自 ProviderSettings 的既有设计)

1. **OS 凭据态**(CLI 自有):`/claude-status`、`/codex-status` 探测;
   claude 的 Login/Re-login/Logout 仅 Tauri(web 模式给终端提示),codex
   永远只给终端提示(无 Tauri IPC)。登录 600s 倒计时自动 SIGTERM
   (`cancelClaudeLogin`)的机制原样搬入。
2. **Provider 记录态**(NarraNexus 自有):props 是**原始 `providers`
   列表**(第 4 轮:claudeCard/hasCodex 两个按厂商写死的 prop 被打回),
   订阅 source 字面量的派生**只在本组件内**——接第三个订阅厂商只动
   这一个文件;每张卡可以有自己的"已连接"判据(claude 的 token 运输层
   看 auth_type,不是行存在性)。写入走 **parent 的 `addProvider`**,
   parent 成功后刷新自己的列表,记录态经 props 回流。

## 无 onConnected(被删的死接口)

第一稿有 `onConnected` 回调(连接成功即通知父级跳转),Owner 否决自动
跳转后没有任何生产调用方传它,本地 review 判为 YAGNI 死接口并删除——
三个 handler 直接 `await addProvider(...)`;setup-token 路径仍依赖
addProvider 的返回值决定是否清输入框(失败保留输入供重试,有测试钉住)。
**不要**因为"以后可能要跳转"把它加回来:Owner 已明确否决该交互。

## 云端门禁(在本组件内,真实生效;第 3 轮改为"解释而非空白")

status 路由对 cloud 非 staff 返回 `allowed: false`(与 403 OAuth 卡型
同一个谓词)。本组件读到任一 `allowed === false` 时渲染**一行说明**
(oauthCloudManaged,testid subscription-cloud-managed)——第 2 轮版本
return null,review 指出 Settings 的 Sign-in tab 会呈现"先闪后空白",
读作页面坏了。配套 `useOauthAllowed()` hook(第 4 轮拆到
useOauthAllowed.ts 独立文件,带 `enabled` 参数推迟探测;失败 fail-open
true——探测失败不是判决,后端 403 才是边界):
ProviderSettings 用它把 Sign-in **tab 本身**从 tab 数组里去掉(入口层
不该指向一个被门禁的面板)。判据必须 `=== false`:local 与 cloud-staff
下该字段是 undefined,truthiness 判断会把本组件要修的 P0(本地订阅)
一起关掉。SetupPage 仍保留 `mode !== 'cloud-web'` 外门(顺带隐藏区块
标题)。后端 403 始终是真正的安全边界。

## 状态探测的失败面(第 3 轮 Minor 1/2/3)

- status 是**每卡三态**(null 探测中 / 'error' 失败行+Retry / payload)
  ——第 3 轮只处理"两条都失败",单条 5xx 仍永卡"Checking status…",
  第 4 轮打回。allowed 门排在失败态之前(探测成功 + allowed:false 是
  判决不是失败);Retry 重probe 两条、不重置 allowed;响应用**单调
  generation 计数**做 stale 守卫(不用 unmount-cancel——手动 Retry 不能
  被自己取消)。测试:both-fail 两条 Retry 行 + single-fail 孪生用例。
- Add as Provider 按钮 POST 期间 disabled + Loader2("Adding…")——与
  Test 按钮同一课:无响应按钮读作卡死。
- status 探测的 useCallback 依赖 `userId`:allowed 是 per-user 的
  (staff 标志),切账号必须重探。传输层走 api.getClaudeStatus /
  getCodexStatus(见 providersApi.md 的定位变更)。

## 卡内子组件(review 第 2 轮 Minor 7)

两张卡近百行的同构体收拢为文件内局部组件:`CliStatusLine`(状态点 +
身份行 + 过期)与 `ProviderRecordRow`(记录态三分支)。**i18n 文案以
已翻译字符串经 props 传入**——两卡 key 不同(addedAsProvider vs
codexAddedAsProvider 等),在子组件里按前缀拼 key 会让 codex 文案静默
回落。

## 搬运保真说明

JSX(claude 卡 A/B/C 三段 + codex 卡)、`CLAUDE_LOGIN_TIMEOUT_SEC`、
`formatCountdown` / `formatExpiresAt` 均从 ProviderSettings 原样搬入,
setup-token 的"原位升级保槽位"注释与行为不变。两张卡有
`data-testid`(claude-connect-card / codex-connect-card)——按钮文案共用
同一 i18n key,测试必须 `within()` 限定。测试:
`__tests__/SubscriptionConnect.test.tsx`(claude add / codex add /
token 成功清输入 / 失败留输入 / allowed:false 渲染**说明行** / 双 fail
两条 Retry / 单 fail 孪生 / local 双卡),
mock 的两个 status 路由返回**可区分**的 payload,防止 claude/codex
断言互相顶替。
