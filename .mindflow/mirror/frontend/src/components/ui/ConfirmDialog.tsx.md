---
code_file: frontend/src/components/ui/ConfirmDialog.tsx
last_verified: 2026-07-30
stub: false
---

# ConfirmDialog.tsx — 应用内 confirm / alert 原语

## 为什么存在

**wry（Tauri 的 webview）不渲染 `window.confirm` / `window.alert` /
`window.prompt`**：调用直接 resolve 成 falsy，什么都不发生。于是 `if (!window.confirm(…))
return;` 在桌面端等于「用户点了取消」——按钮点了没反应、没弹窗、没报错；而 alert 则是
提示彻底消失。两者都破坏铁律 #7（DMG 与浏览器行为必须一致），而且**破坏得无声**。

这个文件是两者的替代：`useConfirm()` 返回 `{ confirm, alert, dialog }`，`dialog` 必须被
调用方挂进 JSX —— 漏挂会让 promise 永不 resolve、按钮变哑，这个失效比它替代的原生问题
更难查。

## 2026-07-30 — 新增 `useNotice()`：把通知 chrome 收进一处

`useConfirm` 之上的薄封装，返回 `{ confirm, notifyError, notifyDone, notifyPending,
dialog }`。起因是原生 alert 清扫的第一版把同一块 chrome（title / okText / danger）在 6 个
文件里逐字复制了 9 遍（评审点名）——调用方真正关心的只有 message。

三个动词**不能合并**：标题不可互换。`noticeTitle` 的十个译法都是「请稍候」语义
（稍等一下 / 少しお待ちください / Одну секунду），放在「已保存到 ~/Downloads/x.pdf」上面
每种语言都读成「还在进行中」。所以完成走 `doneTitle`、未就绪走 `noticeTitle`、失败走
`actionFailedTitle` + danger。

**顺带补掉一个 i18n 洞**：`useConfirm` 自己的默认值 `'Notice'` / `'OK'` / `'Confirm'` 是
硬编码英文、不走 i18n，调用方不传就让非英语用户看到英文外壳。兜底现在写在 `useNotice`
里一次，**没有去改 `useConfirm`** —— 那是 20+ 调用方共用的原语，在一个 bug 修复里动它
不划算。

`confirm` 被重新导出，是为了同时需要「提问」和「通知」的组件只持有一个实例、只挂一个
`dialog`（[[TeamManagementModal]] 正是这种情况）。传**专属**标题的场合（如
[[ModelDefaultsSettings]] 的 "Staff only in cloud"）继续直接用 `useConfirm().alert` ——
`useNotice` 只负责通用 chrome。

## 踩过的坑

- **禁令要靠自动化，不能靠自觉。** 这类 bug 两轮（订阅确认框、9 处 alert）都是靠人读代码
  发现的，从来不是测试发现的 —— 因为单元测试 stub 掉了 `window.confirm`，反而什么都没
  证明。现在由 `lib/__tests__/no-native-dialogs.test.ts` 做仓库级扫描，且同批把 vitest
  接进了 CI（此前全仓库没有任何 CI 执行测试）。
- **不禁裸 `alert(`**：本 hook 返回的函数就叫 `alert`，`const { alert } = useConfirm()`
  之后的调用会被误报。禁令因此覆盖 `window|globalThis|self` 前缀 + 从这三者解构。
- **叠加 Dialog 时 body 滚动锁会被提前解开**（[[Dialog]] 卸载时无条件清 overflow）——
  见 `reference/self_notebook/todo/2026-07-30-dialog-scroll-lock-refcount.md`。
