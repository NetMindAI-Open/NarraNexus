---
code_file: tests/browser/test_route_mounting.py
last_verified: 2026-09-24
stub: false
---

# 浏览器插件开关的真实路由契约

在独立进程、临时插件注册表中导入真实后端，验证禁用浏览器插件时 HTTP 与
WebSocket 路由一起消失。使用真实挂载结果避免只测试注册辅助函数，却遗漏
后端 import 阶段无条件开放路由的回归。
