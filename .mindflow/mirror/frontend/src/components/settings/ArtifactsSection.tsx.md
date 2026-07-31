---
code_file: frontend/src/components/settings/ArtifactsSection.tsx
last_verified: 2026-07-30
stub: false
---

## 2026-07-30 — 原生 alert 换成应用内通知

wry（Tauri webview）**不渲染** `window.alert`，调用直接返回、什么都不发生。所以桌面端
批量删除失败时只有「确认弹窗没关」这一个弱信号，没有原因。改用 [[ConfirmDialog]] 的 `useNotice()`，与仓库既有的 20+ 处 confirm 先例同一条路。

**chrome 不在调用点重复**：标题 / OK 文案 / danger 由 `useNotice` 提供，调用点只写
message。第一版把这三行在 6 个文件里复制了 9 遍（评审点名），改文案要改 9 处。这同时把
`useConfirm` 默认值 `'Notice'` / `'OK'` 硬编码英文、不走 i18n 的洞补在一处 ——
不必去动那个 20+ 调用方共用的原语。共享 key
`common.{noticeTitle,doneTitle,actionFailedTitle,ok}`，10 语言。

`notifyDone` 与 `notifyPending` 是分开的：`noticeTitle` 的 10 个译法都是「请稍候」语义
（稍等一下 / 少しお待ちください / Одну секунду），拿它当成功提示的标题会让用户以为还在
进行中 —— 所以成功走 `doneTitle`。

用一条**仓库级静态契约测试**钉住（`lib/__tests__/no-native-dialogs.test.ts`）：扫描全部
源文件，禁止任何 `window.alert/confirm/prompt` 调用。这类 bug 前两轮都是靠人读代码发现的
—— 单元测试反而 stub 掉了 `window.confirm` 因而什么都没证明。grep 是唯一能覆盖「还没被
写出来的文件」的断言。


# ArtifactsSection.tsx — 设置页的制品管理区

## 为什么存在

Settings → Artifacts 面板：列出当前用户的制品、支持多选批量删除。挂在
[[SettingsPage]] 的 `artifacts` 导航项下，**按需挂载**（只有该面板激活时才渲染），
所以它的 fetch 不会在其他面板上触发。

## 上下游

- 上游：[[SettingsPage]]（唯一挂载点）。
- 下游：`artifactsApi.bulkDelete` / 列表读取（[[artifactsApi]]）。

> 本文件此前无 mirror（2026-07-30 补建，起因是原生 alert 清扫）。上面那条日期条目
> 之外的历史意图未回溯，后续改动时按铁律 #10 逐步补齐。
