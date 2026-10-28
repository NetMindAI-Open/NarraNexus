---
code_file: tests/release/test_browser_packaging.py
last_verified: 2026-10-08
stub: false
---

## 2026-10-08（PR #410 review）

desktop 包含浏览器、cloud 不包含（review C2）分成两条断言。

# 浏览器功能的发行包契约

构建并从真实 wheel 导入插件与手动安装入口，防止源码环境可用但桌面包遗漏
manifest 或模块。浏览器二进制与 Playwright 不随插件成为强制依赖，运行时
缺失时仍能加载插件并返回安装提示。

同时约束桌面和云端分发清单、依赖锁与发行 smoke import，使源码启动和
打包启动都能发现浏览器功能；这些测试不替代完整 DMG 构建验证。
