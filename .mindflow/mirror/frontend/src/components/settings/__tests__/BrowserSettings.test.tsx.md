---
code_file: frontend/src/components/settings/__tests__/BrowserSettings.test.tsx
last_verified: 2026-09-23
stub: false
---

# BrowserSettings regressions

模式开关验证成功回执后才切换、保存期间来源和刷新互斥、失败保持原值并展示错误，
以及云端不暴露本地窗口开关。

来源选择测试覆盖保存期间的禁用、成功回执后更新、失败保留原选择、独立登录状态
提示，以及系统 Chrome 不可用时不提供无关的托管安装操作。

The install destination must stay useful across missing/broken runtimes, transport
faults and failed install responses. Deferred requests prove that pending starts
cannot expose duplicate installation and cancellation acceptance cannot claim
completion. Fake timers cover nonoverlapping progress polls and recovery after a
transient failure. Accessible progress names, unknown totals, extraction and
verification are checked independently of visual styling. Injected endpoints
avoid real downloads and cancellation.

补充手动恢复集成：显示运行时返回的原文，安装失败且状态查询故障后仍保留安装
响应里的恢复内容；确认 ready 后移除恢复命令。
