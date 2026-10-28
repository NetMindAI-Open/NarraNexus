---
code_file: frontend/src/components/telemetry/TelemetryNotice.tsx
stub: false
last_verified: 2026-08-13
---

## 2026-08-13 — positioning moved to MainLayout's shared slot

The outer card dropped its own `fixed bottom-4 left-1/2 -translate-x-1/2 z-50
max-w-md w-[…]` and is now a plain `w-full pointer-events-auto` card.
[[MainLayout.tsx]] positions it (and [[WebAnalyticsNotice.tsx]]) inside one
shared bottom-anchored `flex-col` slot so the two cloud disclosures stack rather
than overlap. Behaviour/gating unchanged — layout only.

# TelemetryNotice.tsx — 遥测首次告知(notice-and-choice 的 notice 半边)

## 为什么存在

遥测默认值(off→meta;托管沙盒经默认层跑 full)与本横幅**同一
变更落地**(默认值与同意基础不可分批)。一次性、每浏览器 profile 一次(localStorage,沿用 HelpButton
的 `_v1` key 模式,存储异常 fail-closed 不纠缠)。

## 设计决策(每条都有反面教材)

- **只在遥测实际激活时展示**(先 GET consent,mode=off 不展示):对
  部署关掉遥测的用户说"我们在发日志"是假话;
- **off 时不烧 seen 标记**:部署以后翻开,用户仍应得到告知;
- localStorage 只记"**展示过**",绝不记同意本身——同意状态在服务端
  标记文件里;清浏览器缓存只会让横幅再现,**不可能被读成重新授权**;
- "打开设置"深链 `?tab=privacy`(SettingsPage nav 测试守住),点击
  同时落 seen——横幅的两个出口都算"已告知";
- **managed 变体**(预审修):`controllable=false` 时换 bodyManaged
  文案并**去掉设置按钮**——对开关被禁用的用户承诺"可去设置关闭"
  是假话;consent GET 失败则不展示也不烧标记(做不到诚实的告知,
  宁可推迟到下次加载)。

Tests: `__tests__/TelemetryNotice.test.tsx`。
