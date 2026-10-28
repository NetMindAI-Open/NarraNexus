---
code_file: tests/browser/test_runtime_launch.py
last_verified: 2026-09-23
stub: false
---

## 2026-09-23 多页面支持

发现接口验证浏览器级 /json/version，替身实现 Target.createTarget/attachToTarget，
防止测试继续把单页断开误当成整个浏览器退出。启动错误、取消和 stderr 覆盖保持。

# Chromium 进程归属与退出测试

注入启动、发现和 socket 替身，验证失败或取消后不遗留浏览器进程和 profile 锁。
正常退出必须先请求 Browser.close，让 Cookie 等状态完成落盘；无响应进程才升级到信号清理。
测试中的短退出宽限期只用于验证清理逻辑，不限制真实智能体的操作时长。
启动 stderr 与代理测试保留真实集成中遇到的故障原因，避免再次只显示不明超时或 502。
策略传递用例改为独立高级脚本策略，普通站点访问禁用字段已删除，不再验证网址拒绝。
