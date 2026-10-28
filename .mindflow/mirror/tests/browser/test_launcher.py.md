---
code_file: tests/browser/test_launcher.py
last_verified: 2026-09-24
stub: false
---

# 浏览器启动与配置目录隔离

保护启动参数和 CDP 就绪探测，端口已打开不等于浏览器已经可以接收协议请求。
配置目录必须对同一 Agent 和命名配置保持稳定，同时隔离不同 Agent 和登录身份；
外部标识不能逃逸配置根目录。测试不依赖真实浏览器安装。
