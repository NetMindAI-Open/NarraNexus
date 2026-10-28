---
code_file: tests/browser/test_cdp.py
last_verified: 2026-09-23
stub: false
---

# CDP 通信和输入契约

用可控 socket 验证响应关联、读循环生命周期和逐帧确认，防止首帧后停止或关闭时遗留任务。
输入白名单测试限制允许传入的协议形状；真实表单回归对应的 Enter 用例要求按下时携带回车，
释放与快捷键不产生字符。替身测试用于精确诊断，原生表单提交由真实 Chromium 用例验证。
