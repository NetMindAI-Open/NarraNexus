---
code_file: frontend/src/components/settings/PrivacySettings.tsx
stub: false
last_verified: 2026-08-13
---

## 2026-08-13 — analytics toggle now also gates third-party GTM

`toggleAnalytics` governs BOTH the first-party product-analytics row AND the
cloud web third-party GTM tag ([[webAnalytics.ts]]) — the `analyticsDesc` copy
(10 locales) was updated to disclose this. On turning it OFF, after
`setAnalyticsOptOut(true)` succeeds it: (1) calls `markWebAnalyticsConsentRevoked()`
to close the in-flight `initWebAnalytics()` window, and (2) `window.location.reload()`
**only if `isWebAnalyticsLoaded()`** — GTM can't be un-loaded, so a reload is how
OFF takes effect now; but desktop / local / dev / self-host never loaded it and
must not eat a pointless reload (dropping WS + streaming chat + unsaved edits).
The reload is inside the `try` after the PUT, so the `catch` (revert) path never
reloads. This is the reachable panel; the old [[SettingsModal.tsx]] never mounts.

# PrivacySettings.tsx — "哪些数据离开这台机器"的两个开关

## 为什么存在

两个**作用域刻意不同**的同意,文案与行为都不许混淆:

- **产品分析**:per-USER,DB 行(`/settings/analytics`)。后端 06-08
  就有,UI 一直困在未挂载的 SettingsModal 里——本组件是它第一次
  真正可达的家;
- **诊断遥测**:per-MACHINE 标记文件(`/settings/telemetry`)。
  `controllable=false`(env 覆盖或多租户云)时开关**禁用并说明**,
  而非隐藏——消失的开关读作"没有遥测",那是假话。

## 设计决策

- analytics 开关乐观翻转、失败回滚;**遥测开关改为 reconcile**
  (预审修):每次 PUT 后(无论成败)重新 GET 服务端状态——开关
  永远落在真实状态上,绝不落在客户端捏造的猜测上;写失败必须出声
  (telemetryError note)——静默失败的退出开关是隐私控件最坏的
  失败形态;
- **GET 失败是独立状态**:渲染"未勾选的开关"会被读成"遥测是关的"
  而它可能正在发——隐私面板唯一不许犯错的方向;改为 unavailable
  文案替代整行,不渲染开关;
- 遥测行 note 按 `managed_by` 三分:cloud → "由管理员管理"(把内置
  默认归因给没人设的 env 变量是谎言)、env → 部署 env 说明、可控 →
  时效说明(关闭数秒生效;仅当启动时已关闭,开启才需重启);
- 组件不自带 SectionHeader,由 SettingsPage 的 privacy 分支包裹
  (与 ModelDefaultsSettings 同款分工)。

Tests: `__tests__/PrivacySettings.test.tsx`(翻转/回滚/禁用守卫)。
