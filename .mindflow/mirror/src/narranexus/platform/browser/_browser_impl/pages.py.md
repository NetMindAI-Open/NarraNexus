---
code_file: src/narranexus/platform/browser/_browser_impl/pages.py
last_verified: 2026-09-23
stub: false
---

## 2026-09-23 多页面支持

TargetInfo 标题可能滞后。每页在独立 world 安装固定 metadata observer，通过 Runtime
binding 推送 document.title/location.href，覆盖动态标题和新文档。旧 TargetInfo 不覆盖
已经观察到的标题；普通导航及 SPA URL 变化仍由 Target 事件补充。所有变更与动作共用
操作锁；销毁中的目标不重新 attach，停止监听排空队列并释放等待者。无数据库变更。

# 浏览器页面集合

浏览器连接负责进程寿命，页面连接负责独立的输入与画面。Target 事件维护页面集合、标题、
网址和 opener，不把 iframe 或内部 WebUI 当作标签。页面变更和操作共用锁，点击与释放不能
分散到两个标签。关闭一个页面只移除该页，活动页关闭后优先回到 opener，最后一页关闭后
创建空白页。用户观看选择由流连接维护，不修改 Agent 的活动页。无需数据库或新模块。
