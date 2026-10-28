---
code_file: tests/browser/test_downloader.py
last_verified: 2026-09-24
stub: false
---

# 托管运行时的可靠安装

把安装成功定义为目标平台的浏览器可以运行且版本匹配，而不是 ZIP 解压成功。
覆盖路径穿越、危险符号链接、损坏归档、错误 Range 响应和失败后重试，
并保留 macOS framework 所需的合法符号链接。

并发及跨进程测试约束安装锁、进度共享和取消传递；取消解压必须等待工作线程
结束后才能解锁，防止后续安装与前一次写入互相覆盖。
